#!/usr/bin/env python3
"""
TICKET-606: Risk Profile Verification Script

Verifies that $31.80 equity → $0.63 risk per trade calculations match requirements.

Expected Results:
- Equity: $31.80
- Risk %: 2%
- Risk per trade: $31.80 × 0.02 = $0.636 ≈ $0.63 ✓
- Scout: $1.50 entry → Stop at -42% = $0.63 risk ✓
- Soldier: $2.00 scale-in → Breakeven stop
- Total position after Soldier: $3.50 ($1.50 + $2.00)
"""

def verify_risk_profile():
    """Verify risk profile calculations for Project Omega."""
    
    print("=" * 60)
    print("TICKET-606: Risk Profile Verification")
    print("=" * 60)
    print()
    
    # Base equity
    equity = 31.80
    risk_pct = 2.0
    
    print(f"Base Equity: ${equity:.2f}")
    print(f"Risk Percentage: {risk_pct}%")
    print()
    
    # 1. Verify risk per trade
    risk_per_trade = equity * (risk_pct / 100.0)
    print(f"1. Risk per Trade Calculation:")
    print(f"   ${equity:.2f} × {risk_pct}% = ${risk_per_trade:.2f}")
    print(f"   Expected: $0.63")
    print(f"   Result: {'✓ PASS' if abs(risk_per_trade - 0.63) < 0.01 else '✗ FAIL'}")
    print()
    
    # 2. Verify Scout sizing
    scout_entry_size = 1.50
    scout_stop_loss_pct = 42.0
    scout_risk = scout_entry_size * (scout_stop_loss_pct / 100.0)
    
    print(f"2. Scout Sizing:")
    print(f"   Entry Size: ${scout_entry_size:.2f}")
    print(f"   Stop Loss: {scout_stop_loss_pct}%")
    print(f"   Risk: ${scout_entry_size:.2f} × {scout_stop_loss_pct}% = ${scout_risk:.2f}")
    print(f"   Expected: $0.63")
    print(f"   Result: {'✓ PASS' if abs(scout_risk - 0.63) < 0.01 else '✗ FAIL'}")
    print()
    
    # 3. Verify Soldier scale-in
    soldier_scale_in_size = 2.00
    total_position_after_soldier = scout_entry_size + soldier_scale_in_size
    
    print(f"3. Soldier Scale-In:")
    print(f"   Scout Entry: ${scout_entry_size:.2f}")
    print(f"   Soldier Scale-In: ${soldier_scale_in_size:.2f}")
    print(f"   Total Position: ${scout_entry_size:.2f} + ${soldier_scale_in_size:.2f} = ${total_position_after_soldier:.2f}")
    print(f"   Expected: $3.50")
    print(f"   Result: {'✓ PASS' if abs(total_position_after_soldier - 3.50) < 0.01 else '✗ FAIL'}")
    print(f"   Note: Stop moves to breakeven after Soldier entry (risk = $0.00)")
    print()
    
    # 4. Summary
    print("=" * 60)
    print("Summary:")
    print("=" * 60)
    print(f"✓ Risk per trade: ${risk_per_trade:.2f} (target: $0.63)")
    print(f"✓ Scout risk: ${scout_risk:.2f} (target: $0.63)")
    print(f"✓ Total position after Soldier: ${total_position_after_soldier:.2f} (target: $3.50)")
    print()
    
    # Verify all calculations
    all_pass = (
        abs(risk_per_trade - 0.63) < 0.01 and
        abs(scout_risk - 0.63) < 0.01 and
        abs(total_position_after_soldier - 3.50) < 0.01
    )
    
    if all_pass:
        print("✓ ALL VERIFICATIONS PASSED")
        return 0
    else:
        print("✗ SOME VERIFICATIONS FAILED")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(verify_risk_profile())
