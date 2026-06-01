"""Strategies endpoint."""

import logging
import uuid as uuid_module
from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.db import get_session
from backend.db.models import Strategy
from backend.api.models import StrategyList, StrategyItem, StrategyConfigResponse, StrategyConfigUpdate
from backend.api.routes.events import log_activity

logger = logging.getLogger(__name__)

router = APIRouter()


# Registry of strategy config schemas
# Maps strategy name to the get_config_schema function
def _get_strategy_config_schema(strategy_name: str) -> dict | None:
    """Get the config schema for a strategy by name."""
    # Lazy import to avoid circular dependencies
    if strategy_name == "mean_reversion":
        from research.strategies.meanrev.config import get_config_schema
        return get_config_schema()
    elif strategy_name in ("trend_following", "momentum"):
        from research.strategies.momentum.config import get_config_schema
        return get_config_schema()
    elif strategy_name in ("macd", "macd_crossover"):
        try:
            from research.strategies.macd.config import get_config_schema
            return get_config_schema()
        except ImportError:
            # MACD module not available yet (Q3-MACD)
            logger.debug(f"MACD config module not available for strategy: {strategy_name}")
            return None
    return None


def _map_status(db_status: str) -> str:
    """
    Map database status to API contract status.
    
    Database has: active, inactive, paused
    API contract expects: active, paused, stopped
    Maps 'inactive' -> 'stopped'
    """
    status_map = {
        "active": "active",
        "inactive": "stopped",
        "paused": "paused",
    }
    return status_map.get(db_status, db_status)


def _get_strategy_interval(strategy) -> str:
    """
    Get the interval for a strategy from its config.
    
    Checks database config first (top-level, then parameters), 
    then falls back to schema defaults.
    Returns '5m' as ultimate fallback.
    """
    try:
        # First check database config
        db_config = strategy.config or {}
        
        # Check top-level interval first (database schema)
        if "interval" in db_config:
            return db_config["interval"]
        
        # Then check parameters.interval (API updates)
        db_params = db_config.get("parameters", {})
        if "interval" in db_params:
            return db_params["interval"]
        
        # Fall back to schema defaults
        schema = _get_strategy_config_schema(strategy.name)
        if schema:
            schema_params = schema.get("parameters", {})
            if "interval" in schema_params:
                return schema_params["interval"]
        
        return "5m"
    except Exception:
        return "5m"


@router.get("/strategies", summary="List registered strategies")
async def list_strategies():
    """
    List all registered strategies.
    
    Returns a list of all strategies from the strategies table,
    including their id, name, status, interval, and created_at timestamp.
    """
    session: Session = get_session()
    try:
        # Query all strategies from the database
        strategies = session.query(Strategy).all()
        
        # Convert to response models
        strategy_items = [
            StrategyItem(
                id=str(strategy.id),
                name=strategy.name,
                status=_map_status(strategy.status),
                created_at=strategy.created_at,
                interval=_get_strategy_interval(strategy),
            )
            for strategy in strategies
        ]
        
        return StrategyList(strategies=strategy_items)
        
    except Exception as e:
        logger.error(f"Error fetching strategies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching strategies")
    finally:
        session.close()


@router.post("/strategies/{strategy_id}/enable", summary="Enable a strategy")
async def enable_strategy(strategy_id: str):
    """
    Enable a strategy by ID or name.
    
    Sets the strategy status to 'active'.
    """
    session: Session = get_session()
    try:
        # Try to find by name first (more common), then by UUID
        strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
        
        if not strategy:
            # Try as UUID
            try:
                uuid_module.UUID(strategy_id)  # Validate it's a UUID
                strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
            except ValueError:
                pass  # Not a valid UUID, strategy stays None
        
        if not strategy:
            raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
        
        strategy.status = "active"
        session.commit()
        
        # Log to activity feed
        log_activity(
            activity_type="system",
            message=f"Strategy enabled: {strategy.name}",
            details={"strategy_id": str(strategy.id), "strategy_name": strategy.name},
        )
        
        return {"message": f"Strategy {strategy.name} enabled", "status": "active"}
        
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Error enabling strategy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("/strategies/{strategy_id}/disable", summary="Disable a strategy")
async def disable_strategy(strategy_id: str):
    """
    Disable a strategy by ID or name.
    
    Sets the strategy status to 'inactive'.
    """
    session: Session = get_session()
    try:
        # Try to find by name first (more common), then by UUID
        strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
        
        if not strategy:
            # Try as UUID
            try:
                uuid_module.UUID(strategy_id)  # Validate it's a UUID
                strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
            except ValueError:
                pass  # Not a valid UUID, strategy stays None
        
        if not strategy:
            raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
        
        strategy.status = "inactive"
        session.commit()
        
        # Log to activity feed
        log_activity(
            activity_type="system",
            message=f"Strategy disabled: {strategy.name}",
            details={"strategy_id": str(strategy.id), "strategy_name": strategy.name},
        )
        
        return {"message": f"Strategy {strategy.name} disabled", "status": "inactive"}
        
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Error disabling strategy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.get("/strategies/{strategy_id}/config", summary="Get strategy configuration")
async def get_strategy_config(strategy_id: str) -> StrategyConfigResponse:
    """
    Get the configuration for a strategy.
    
    Returns strategy parameters, filters, and description.
    Merges database-stored config with default schema values.
    """
    session: Session = get_session()
    try:
        # Try to find by name first (more common), then by UUID
        strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
        
        if not strategy:
            # Try as UUID
            try:
                uuid_module.UUID(strategy_id)
                strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
            except ValueError:
                pass
        
        if not strategy:
            raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
        
        # Get the default schema for this strategy type
        schema = _get_strategy_config_schema(strategy.name)
        
        if not schema:
            # Return minimal config if no schema available
            db_config = strategy.config or {}
            filters = dict(db_config.get("filters", {}))
            filters.setdefault("confidence_buy", 90)
            filters.setdefault("confidence_sell", 90)
            filters.setdefault("min_allowed_grade", "A+")
            return StrategyConfigResponse(
                strategy_id=strategy.name,
                strategy_type=strategy.name,
                parameters=db_config.get("parameters", {}),
                filters=filters,
                description=db_config.get("description", f"Strategy: {strategy.name}"),
                volume_threshold=db_config.get("volume_threshold"),
            )
        
        # Merge database config with defaults (db values take precedence)
        db_config = strategy.config or {}
        
        merged_parameters = {**schema.get("parameters", {})}
        if "parameters" in db_config:
            merged_parameters.update(db_config["parameters"])
        
        merged_filters = {**schema.get("filters", {})}
        if "filters" in db_config:
            merged_filters.update(db_config["filters"])
        # Ensure screener defaults (signal strength + min grade)
        merged_filters.setdefault("confidence_buy", 90)
        merged_filters.setdefault("confidence_sell", 90)
        merged_filters.setdefault("min_allowed_grade", "A+")
        
        return StrategyConfigResponse(
            strategy_id=strategy.name,
            strategy_type=schema.get("strategy_type", strategy.name),
            parameters=merged_parameters,
            filters=merged_filters,
            description=db_config.get("description", schema.get("description", "")),
            volume_threshold=db_config.get("volume_threshold"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching strategy config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.put("/strategies/{strategy_id}/config", summary="Update strategy configuration")
async def update_strategy_config(strategy_id: str, config_update: StrategyConfigUpdate) -> StrategyConfigResponse:
    """
    Update the configuration for a strategy.
    
    Accepts partial updates - only provided fields are updated.
    Returns the updated configuration.
    """
    session: Session = get_session()
    try:
        # Try to find by name first (more common), then by UUID
        strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
        
        if not strategy:
            # Try as UUID
            try:
                uuid_module.UUID(strategy_id)
                strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
            except ValueError:
                pass
        
        if not strategy:
            raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
        
        # Get current config or initialize empty
        current_config = dict(strategy.config) if strategy.config else {}
        
        # Update parameters if provided
        if config_update.parameters is not None:
            if "parameters" not in current_config:
                current_config["parameters"] = {}
            
            # Handle interval specially - store at top level for consistency
            if "interval" in config_update.parameters:
                current_config["interval"] = config_update.parameters["interval"]
            
            current_config["parameters"].update(config_update.parameters)
        
        # Update filters if provided
        if config_update.filters is not None:
            if "filters" not in current_config:
                current_config["filters"] = {}
            current_config["filters"].update(config_update.filters)
        
        # Update volume_threshold if provided
        if config_update.volume_threshold is not None:
            current_config["volume_threshold"] = config_update.volume_threshold
        
        # Save to database
        # Use flag_modified to ensure SQLAlchemy detects JSONB changes
        strategy.config = current_config
        flag_modified(strategy, "config")
        session.commit()
        
        # Reset this strategy's metrics so P&L and accuracy reflect the new config
        try:
            from backend.risk.metrics import reset_strategy_metrics_for_ids
            reset_strategy_metrics_for_ids([str(strategy.id), strategy.name])
            logger.info(f"Reset metrics for strategy {strategy.name} after config update")
        except Exception as e:
            logger.warning(f"Failed to reset strategy metrics after config update: {e}")

        # Log to activity feed
        log_activity(
            activity_type="system",
            message=f"Strategy config updated: {strategy.name} (metrics reset)",
            details={
                "strategy_id": str(strategy.id),
                "strategy_name": strategy.name,
                "updated_parameters": config_update.parameters,
                "updated_filters": config_update.filters,
            },
        )

        # Return updated config using the GET logic
        schema = _get_strategy_config_schema(strategy.name)
        
        if not schema:
            filters = dict(current_config.get("filters", {}))
            filters.setdefault("confidence_buy", 90)
            filters.setdefault("confidence_sell", 90)
            filters.setdefault("min_allowed_grade", "A+")
            return StrategyConfigResponse(
                strategy_id=strategy.name,
                strategy_type=strategy.name,
                parameters=current_config.get("parameters", {}),
                filters=filters,
                description=current_config.get("description", f"Strategy: {strategy.name}"),
                volume_threshold=current_config.get("volume_threshold"),
            )
        
        merged_parameters = {**schema.get("parameters", {})}
        if "parameters" in current_config:
            merged_parameters.update(current_config["parameters"])
        
        merged_filters = {**schema.get("filters", {})}
        if "filters" in current_config:
            merged_filters.update(current_config["filters"])
        merged_filters.setdefault("confidence_buy", 90)
        merged_filters.setdefault("confidence_sell", 90)
        merged_filters.setdefault("min_allowed_grade", "A+")
        
        return StrategyConfigResponse(
            strategy_id=strategy.name,
            strategy_type=schema.get("strategy_type", strategy.name),
            parameters=merged_parameters,
            filters=merged_filters,
            description=current_config.get("description", schema.get("description", "")),
            volume_threshold=current_config.get("volume_threshold"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating strategy config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()
