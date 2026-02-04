# MSDD v3.0 Integration Test Execution Guide

## Quick Start

### Prerequisites

1. **Install pytest and dependencies:**
```bash
cd backend
pip install pytest pytest-asyncio pytest-cov
```

2. **Set up test environment:**
```bash
# Ensure backend is in Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Run Tests

```bash
# From project root
pytest backend/tests/integration/test_msdd_v3_lifecycle.py -v

# With coverage
pytest backend/tests/integration/test_msdd_v3_lifecycle.py --cov=backend --cov-report=html -v

# Run specific test class
pytest backend/tests/integration/test_msdd_v3_lifecycle.py::TestScoutEntryLifecycle -v
```

## Test Structure

The test suite is organized into 10 test classes:

1. **TestScoutEntryLifecycle** - Complete Scout entry flow
2. **TestScaleInLifecycle** - Scale-in and breakeven guard
3. **TestExitScenarios** - All exit mechanisms
4. **TestLiveSlotsOverflow** - LIVE_SLOTS system
5. **TestLiveUniverseRestriction** - Live universe filtering
6. **TestCostminValidation** - Costmin enforcement
7. **TestDynamicRiskRecalculation** - Risk recalculation
8. **TestEdgeCases** - Boundary conditions
9. **TestPerformance** - Performance benchmarks

## Expected Test Results

After fixing any import/module issues, all tests should pass, demonstrating:

- ✅ Complete lifecycle coverage
- ✅ Correct state transitions
- ✅ Proper error handling
- ✅ Performance within limits

## Troubleshooting

### Import Errors
- Ensure `backend` is in PYTHONPATH
- Check that all modules exist and are importable
- Verify test fixtures match actual module structure

### Mock Issues
- Review actual module interfaces
- Adjust mocks to match real implementations
- Use `unittest.mock.patch` for dependency injection

### Async Issues
- Ensure `pytest-asyncio` is installed
- Use `@pytest.mark.asyncio` decorator
- Check async/await usage matches actual code

## Next Steps

1. Install dependencies
2. Run test collection to verify imports
3. Execute tests and fix any issues
4. Expand test coverage as needed
5. Integrate with CI/CD pipeline
