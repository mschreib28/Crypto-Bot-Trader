"""Performance analysis module for calculating trading strategy metrics.

Calculates win rate, risk/reward ratio, consistency metrics, and strategy comparisons
from historical trade data.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class TradeMetrics:
    """Metrics for a single trade."""
    symbol: str
    strategy_id: str
    entry_price: float
    exit_price: Optional[float]
    entry_time: datetime
    exit_time: Optional[datetime]
    quantity: float
    side: str  # "buy" or "sell"
    pnl: Optional[float]
    risk: Optional[float]  # Entry to stop-loss distance
    reward: Optional[float]  # Entry to take-profit distance
    risk_reward_ratio: Optional[float]
    is_winner: Optional[bool]


@dataclass
class StrategyPerformance:
    """Performance metrics for a strategy."""
    strategy_id: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    average_win: float
    average_loss: float
    risk_reward_ratio: float
    total_pnl: float
    largest_win: float
    largest_loss: float
    average_holding_time_hours: float
    consistency_score: float  # Lower is better (standard deviation of returns)


class PerformanceAnalyzer:
    """Analyzes trading performance from Redis positions."""
    
    def __init__(self, db_session=None):
        """
        Initialize performance analyzer.
        
        Args:
            db_session: SQLAlchemy session (optional, not used but kept for compatibility)
        """
        self.db_session = db_session
    
    def analyze_trades(
        self, 
        days_back: int = 30,
        strategy_id: Optional[str] = None
    ) -> List[TradeMetrics]:
        """
        Analyze trades from Redis positions.
        
        Args:
            days_back: Number of days to look back (default: 30)
            strategy_id: Filter by strategy ID (optional)
            
        Returns:
            List of TradeMetrics for each trade
        """
        from backend.redis import get_redis_client
        import json
        
        cutoff_date = datetime.utcnow().replace(tzinfo=None) - timedelta(days=days_back)
        redis_client = get_redis_client()
        
        # Get all positions from Redis (these represent open trades)
        positions_by_symbol = {}
        try:
            for key_bytes in redis_client.scan_iter(match="position:*"):
                key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
                print(f"DEBUG: Processing key: {key}")
                try:
                    data = redis_client.hgetall(key)
                    print(f"DEBUG: Got {len(data)} fields from {key}")
                    if data:
                        # Convert bytes keys/values to strings
                        decoded_data = {}
                        for k, v in data.items():
                            k_str = k.decode() if isinstance(k, bytes) else k
                            v_str = v.decode() if isinstance(v, bytes) else v
                            decoded_data[k_str] = v_str
                        symbol = decoded_data.get("symbol")
                        print(f"DEBUG: Symbol from {key}: {symbol}")
                        if symbol:
                            positions_by_symbol[symbol] = decoded_data
                            print(f"DEBUG: Added {symbol} to positions_by_symbol")
                except Exception as e:
                    print(f"DEBUG: Exception loading {key}: {e}")
                    logger.warning(f"Failed to load position from {key}: {e}")
                    continue
        except Exception as e:
            print(f"DEBUG: Exception scanning Redis: {e}")
            logger.warning(f"Failed to scan Redis for positions: {e}")
        
        logger.info(f"Found {len(positions_by_symbol)} positions in Redis: {list(positions_by_symbol.keys())}")
        
        # Convert positions to TradeMetrics
        trades = []
        processed = 0
        skipped_strategy = 0
        skipped_date = 0
        for symbol, position_dict in positions_by_symbol.items():
            processed += 1
            print(f"DEBUG: Processing {symbol}: strategy={position_dict.get('opened_by_strategy_id', 'unknown')}")
            
            # Get strategy ID from position
            strategy_id_val = position_dict.get("opened_by_strategy_id", "unknown")
            if strategy_id and str(strategy_id_val) != str(strategy_id):
                print(f"DEBUG: Skipping {symbol}: strategy_id {strategy_id_val} != {strategy_id}")
                skipped_strategy += 1
                continue
            
            # Parse entry time
            entry_time_str = position_dict.get("entry_time", "")
            if not entry_time_str:
                logger.warning(f"No entry_time for {symbol}, skipping")
                continue
                
            try:
                # Handle both Z and +00:00 formats
                entry_time_str_clean = entry_time_str.replace('Z', '+00:00')
                entry_time = datetime.fromisoformat(entry_time_str_clean)
                # Convert to UTC naive for comparison
                if entry_time.tzinfo:
                    entry_time_naive = entry_time.replace(tzinfo=None)
                else:
                    entry_time_naive = entry_time
            except Exception as e:
                logger.warning(f"Failed to parse entry_time '{entry_time_str}' for {symbol}: {e}")
                # Don't skip - use current time as fallback
                entry_time_naive = datetime.utcnow()
            
            # Skip if too old (compare naive datetimes)
            if entry_time_naive < cutoff_date:
                print(f"DEBUG: Skipping {symbol}: entry_time {entry_time_naive} < cutoff {cutoff_date}")
                skipped_date += 1
                continue
            
            print(f"DEBUG: Processing {symbol}: strategy={strategy_id_val}, entry_time={entry_time_naive}, pnl={position_dict.get('unrealized_pnl', 'N/A')}")
            logger.info(f"Processing position: {symbol}, strategy={strategy_id_val}, entry_time={entry_time_naive}, pnl={position_dict.get('unrealized_pnl', 'N/A')}")
            
            # Calculate P&L
            pnl = float(position_dict.get("unrealized_pnl", 0.0))
            is_winner = pnl > 0 if pnl is not None else None
            exit_price_str = position_dict.get("current_price")
            exit_price = float(exit_price_str) if exit_price_str else None
            
            # Calculate risk/reward from position data
            entry_price = float(position_dict.get("entry_price", 0.0))
            quantity = float(position_dict.get("quantity", 0.0))
            side = position_dict.get("side", "long")
            
            risk = None
            reward = None
            r_r_ratio = None
            
            stop_loss_str = position_dict.get("stop_loss_price")
            if stop_loss_str and entry_price > 0:
                stop_loss_price = float(stop_loss_str)
                risk = abs(entry_price - stop_loss_price)
                
                # Estimate reward (use 10% take-profit as default if not set)
                if risk:
                    # Default take-profit: 10% for mean reversion, 15% for MACD, 12% for momentum
                    take_profit_pct = 10.0
                    if "macd" in str(strategy_id_val).lower():
                        take_profit_pct = 15.0
                    elif "momentum" in str(strategy_id_val).lower() or "trend" in str(strategy_id_val).lower():
                        take_profit_pct = 12.0
                    
                    reward = entry_price * (take_profit_pct / 100.0)
                    if risk > 0:
                        r_r_ratio = reward / risk
            else:
                # No stop-loss data, estimate from defaults
                # Assume 5% stop-loss (default)
                if entry_price > 0:
                    risk = entry_price * 0.05
                    reward = risk * 2.0  # Assume 2:1 R:R
                    r_r_ratio = 2.0
            
            trade = TradeMetrics(
                symbol=symbol,
                strategy_id=str(strategy_id_val),
                entry_price=entry_price,
                exit_price=exit_price,
                entry_time=entry_time_naive,
                exit_time=None,  # Position still open
                quantity=quantity,
                side="buy" if side == "long" else "sell",
                pnl=pnl,
                risk=risk,
                reward=reward,
                risk_reward_ratio=r_r_ratio,
                is_winner=is_winner,
            )
            trades.append(trade)
            print(f"DEBUG: Added trade: {symbol}, P&L=${pnl:.2f}")
        
        print(f"DEBUG: Processed {len(trades)} trades (skipped: {skipped_strategy} strategy filter, {skipped_date} date filter)")
        logger.info(f"Processed {len(trades)} trades (skipped: {skipped_strategy} strategy filter, {skipped_date} date filter)")
        return trades
    
    def calculate_strategy_performance(
        self,
        trades: List[TradeMetrics]
    ) -> Dict[str, StrategyPerformance]:
        """
        Calculate performance metrics per strategy.
        
        Args:
            trades: List of TradeMetrics
            
        Returns:
            Dict mapping strategy_id to StrategyPerformance
        """
        strategy_trades = defaultdict(list)
        for trade in trades:
            strategy_trades[trade.strategy_id].append(trade)
        
        results = {}
        for strategy_id, strategy_trades_list in strategy_trades.items():
            if not strategy_trades_list:
                continue
            
            # Filter trades with P&L data
            trades_with_pnl = [t for t in strategy_trades_list if t.pnl is not None]
            if not trades_with_pnl:
                continue
            
            winning_trades = [t for t in trades_with_pnl if t.is_winner]
            losing_trades = [t for t in trades_with_pnl if not t.is_winner]
            
            total_trades = len(trades_with_pnl)
            winning_count = len(winning_trades)
            losing_count = len(losing_trades)
            
            win_rate = (winning_count / total_trades * 100) if total_trades > 0 else 0.0
            
            avg_win = sum(t.pnl for t in winning_trades) / winning_count if winning_count > 0 else 0.0
            avg_loss = abs(sum(t.pnl for t in losing_trades) / losing_count) if losing_count > 0 else 0.0
            
            r_r_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
            
            total_pnl = sum(t.pnl for t in trades_with_pnl)
            
            largest_win = max((t.pnl for t in winning_trades), default=0.0)
            largest_loss = min((t.pnl for t in losing_trades), default=0.0)
            
            # Calculate average holding time
            holding_times = []
            for trade in trades_with_pnl:
                if trade.exit_time and trade.entry_time:
                    delta = trade.exit_time - trade.entry_time
                    holding_times.append(delta.total_seconds() / 3600)  # Convert to hours
            
            avg_holding_time = sum(holding_times) / len(holding_times) if holding_times else 0.0
            
            # Calculate consistency (standard deviation of returns)
            returns = [t.pnl for t in trades_with_pnl]
            if len(returns) > 1:
                mean_return = sum(returns) / len(returns)
                variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
                consistency_score = variance ** 0.5  # Standard deviation
            else:
                consistency_score = 0.0
            
            results[strategy_id] = StrategyPerformance(
                strategy_id=strategy_id,
                total_trades=total_trades,
                winning_trades=winning_count,
                losing_trades=losing_count,
                win_rate=win_rate,
                average_win=avg_win,
                average_loss=avg_loss,
                risk_reward_ratio=r_r_ratio,
                total_pnl=total_pnl,
                largest_win=largest_win,
                largest_loss=largest_loss,
                average_holding_time_hours=avg_holding_time,
                consistency_score=consistency_score,
            )
        
        return results
    
    def generate_report(
        self,
        days_back: int = 30,
        output_file: Optional[str] = None
    ) -> str:
        """
        Generate performance analysis report.
        
        Args:
            days_back: Number of days to analyze
            output_file: Optional file path to save report
            
        Returns:
            Report as string
        """
        trades = self.analyze_trades(days_back=days_back)
        strategy_perf = self.calculate_strategy_performance(trades)
        
        report_lines = [
            "# Trading Strategy Performance Analysis",
            f"Analysis Period: Last {days_back} days",
            f"Generated: {datetime.utcnow().isoformat()}",
            "",
            "## Summary",
            f"Total Trades Analyzed: {len(trades)}",
            f"Strategies Analyzed: {len(strategy_perf)}",
            "",
            "## Strategy Performance",
        ]
        
        for strategy_id, perf in sorted(strategy_perf.items(), key=lambda x: x[1].win_rate, reverse=True):
            report_lines.extend([
                f"### {strategy_id}",
                f"- Total Trades: {perf.total_trades}",
                f"- Win Rate: {perf.win_rate:.1f}%",
                f"- Winning Trades: {perf.winning_trades}",
                f"- Losing Trades: {perf.losing_trades}",
                f"- Average Win: ${perf.average_win:.2f}",
                f"- Average Loss: ${perf.average_loss:.2f}",
                f"- Risk/Reward Ratio: {perf.risk_reward_ratio:.2f}:1",
                f"- Total P&L: ${perf.total_pnl:.2f}",
                f"- Largest Win: ${perf.largest_win:.2f}",
                f"- Largest Loss: ${perf.largest_loss:.2f}",
                f"- Avg Holding Time: {perf.average_holding_time_hours:.1f} hours",
                f"- Consistency Score: {perf.consistency_score:.2f} (lower is better)",
                "",
            ])
        
        # Find best strategy
        if strategy_perf:
            best_strategy = max(strategy_perf.items(), key=lambda x: x[1].win_rate)
            report_lines.extend([
                "## Recommendations",
                f"1. **Best Strategy**: {best_strategy[0]} with {best_strategy[1].win_rate:.1f}% win rate",
                f"2. Focus optimization efforts on strategies with win rate < 50%",
                f"3. Strategies with R:R ratio < 2.0 need take-profit adjustments",
                f"4. High consistency scores indicate need for stricter filters",
            ])
        
        report = "\n".join(report_lines)
        
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report)
            logger.info(f"Report saved to {output_file}")
        
        return report


def main():
    """Run performance analysis and generate report."""
    analyzer = PerformanceAnalyzer()
    report = analyzer.generate_report(days_back=30)
    print(report)
    
    # Save to reports directory
    import os
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    output_file = os.path.join(reports_dir, "baseline_performance.md")
    analyzer.generate_report(days_back=30, output_file=output_file)


if __name__ == "__main__":
    main()
