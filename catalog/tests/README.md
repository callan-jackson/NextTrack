# Test Suite

## Structure

```
catalog/tests/
    __init__.py          # Re-exports all test classes for backward compat
    test_models.py       # Model properties, form validation
    test_services.py     # Recommendation engine, feature vectors, diversity
    test_views_api.py    # REST API endpoint tests
    README.md            # This file
```

## Running Tests

```bash
# All tests
make test

# With coverage
make test-cov

# Single module
python manage.py test catalog.tests.test_models
python manage.py test catalog.tests.test_services
python manage.py test catalog.tests.test_views_api
```

## Naming Convention

| Element        | Pattern                              | Example                             |
|---------------|--------------------------------------|-------------------------------------|
| File          | `test_<module>.py`                   | `test_services.py`                  |
| Class         | `<Feature>TestCase`                  | `EuclideanDistanceTestCase`         |
| Method        | `test_<scenario>_<expected>`         | `test_empty_input_returns_empty`    |
| Fixtures      | `setUpTestData` (class) preferred    | Faster than `setUp` per-method      |

All test classes inherit from `django.test.TestCase` (unit) or
`rest_framework.test.APITestCase` (API integration).
