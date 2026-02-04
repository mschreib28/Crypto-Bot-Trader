#!/usr/bin/env python3
"""
TICKET-608: 48-Hour Rule Monitoring Script

Monitors and analyzes positions closed by 48-hour rule.
Tracks percentage of positions closed by time-decay and alerts if >30%.

Usage:
    python3 scripts/monitor_48h_rule.py [--days N] [--alert-threshold PCT]
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.redis import get_redis_client
from backend.redis.keys import EVENTS_LOG_KEY


def get_48h_closures(days: int = 7) -> List[Dict[str, Any]]:
    """
    Get positions closed by 48-hour rule from activity log.
    
    Args:
        days: Number of days to look back
        
    Returns:
        List of closure events with details
    """
    redis_client = get_redis_client()
    
    # Get activity log entries
    events_json = redis_client.lrange(EVENTS_LOG_KEY, 0, -1)
    
    closures = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
    
    for event_json in events_json:
        try:
            event = json.loads(event_json)
            
            # Check if it's an EXIT_FORCED event with 48-hour rule reason
            if (event.get("type") == "EXIT_FORCED" and 
                event.get("details", {}).get("reason") == "opportunity_filter_48h"):
                
                # Check timestamp
                event_time = datetime.fromisoformat(event["timestamp"].replace('Z', '+00:00'))
                if event_time >= cutoff_time:
                    closures.append(event)
                    
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            continue
    
    return closures


def get_all_exits(days: int = 7) -> List[Dict[str, Any]]:
    """Get all position exits (forced or otherwise) from activity log."""
    redis_client = get_redis_client()
    
    events_json = redis_client.lrange(EVENTS_LOG_KEY, 0, -1)
    
    exits = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
    
    for event_json in events_json:
        try:
            event = json.loads(event_json)
            
            # Check if it's an exit event
            if event.get("type") == "EXIT_FORCED":
                event_time = datetime.fromisoformat(event["timestamp"].replace('Z', '+00:00'))
                if event_time >= cutoff_time:
                    exits.append(event)
                    
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    
    return exits


def analyze_48h_rule(days: int = 7, alert_threshold: float = 30.0) -> Dict[str, Any]:
    """
    Analyze 48-hour rule closures and generate report.
    
    Args:
        days: Number of days to analyze
        alert_threshold: Alert if percentage exceeds this threshold
        
    Returns:
        Analysis results dictionary
    """
    print("=" * 60)
    print(f"TICKET-608: 48-Hour Rule Monitoring ({days} days)")
    print("=" * 60)
    print()
    
    # Get closures
    closures_48h = get_48h_closures(days=days)
    all_exits = get_all_exits(days=days)
    
    total_48h_closures = len(closures_48h)
    total_exits = len(all_exits)
    
    # Calculate percentage
    percentage = 0.0
    if total_exits > 0:
        percentage = (total_48h_closures / total_exits) * 100.0
    
    print(f"Analysis Period: Last {days} days")
    print(f"Total Position Exits: {total_exits}")
    print(f"48-Hour Rule Closures: {total_48h_closures}")
    print(f"Percentage: {percentage:.1f}%")
    print()
    
    # Alert if threshold exceeded
    if percentage > alert_threshold:
        print(f"⚠️  ALERT: {percentage:.1f}% exceeds {alert_threshold}% threshold!")
        print("   Consider tightening entry filters or reviewing strategy performance.")
        print()
    else:
        print(f"✓ Percentage ({percentage:.1f}%) within acceptable range (< {alert_threshold}%)")
        print()
    
    # Breakdown by symbol
    if closures_48h:
        print("Breakdown by Symbol:")
        symbol_counts = {}
        for closure in closures_48h:
            symbol = closure.get("details", {}).get("symbol", "unknown")
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        
        for symbol, count in sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {symbol}: {count} closure(s)")
        print()
    
    # Breakdown by strategy
    if closures_48h:
        print("Breakdown by Strategy:")
        strategy_counts = {}
        for closure in closures_48h:
            strategy = closure.get("details", {}).get("strategy", "unknown")
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
        
        for strategy, count in sorted(strategy_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {strategy}: {count} closure(s)")
        print()
    
    return {
        "days": days,
        "total_exits": total_exits,
        "total_48h_closures": total_48h_closures,
        "percentage": percentage,
        "alert_triggered": percentage > alert_threshold,
        "alert_threshold": alert_threshold,
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Monitor 48-hour rule position closures")
    parser.add_argument("--days", type=int, default=7, help="Number of days to analyze (default: 7)")
    parser.add_argument("--alert-threshold", type=float, default=30.0, 
                       help="Alert threshold percentage (default: 30.0)")
    
    args = parser.parse_args()
    
    try:
        results = analyze_48h_rule(days=args.days, alert_threshold=args.alert_threshold)
        
        # Exit with error code if alert triggered
        if results["alert_triggered"]:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
