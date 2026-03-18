"""Tests for the NextTrack recommendation API.

Split from catalog/tests.py into per-concern modules.
All test classes are re-exported here for backward compatibility with
``python manage.py test catalog``.
"""

from catalog.tests.test_models import *          # noqa: F401,F403
from catalog.tests.test_services import *        # noqa: F401,F403
from catalog.tests.test_views_api import *       # noqa: F401,F403
from catalog.tests.test_integration import *     # noqa: F401,F403
from catalog.tests.test_views_web import *       # noqa: F401,F403
