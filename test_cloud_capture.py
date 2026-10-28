from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch
import cloud_capture as cloud


class CloudTargetGuards(TestCase):
    def test_current_date_cannot_be_production_target(self):
        with patch.object(cloud.sys, "argv", ["cloud_capture.py", "production"]), patch.object(cloud.csl, "now", return_value=datetime(2026, 10, 28, tzinfo=timezone.utc)), patch.object(cloud, "publish_capture") as capture:
            cloud.main()
            capture.assert_not_called()

    def test_annual_cron_cannot_reuse_target_in_2028(self):
        with patch.object(cloud.sys, "argv", ["cloud_capture.py", "production"]), patch.object(cloud.csl, "now", return_value=datetime(2028, 1, 15, 11, tzinfo=timezone.utc)), patch.object(cloud, "publish_capture") as capture:
            cloud.main()
            capture.assert_not_called()

    def test_late_target_day_start_fails_honestly(self):
        with patch.object(cloud.sys, "argv", ["cloud_capture.py", "production"]), patch.object(cloud.csl, "now", return_value=datetime(2027, 1, 15, 12, 21, tzinfo=timezone.utc)), patch.object(cloud, "publish_capture") as capture:
            with self.assertRaises(RuntimeError):
                cloud.main()
            capture.assert_not_called()

    def test_observations_stop_after_requested_preservation_period(self):
        with patch.object(cloud.csl, "now", return_value=datetime(2027, 1, 20, tzinfo=timezone.utc)), patch.object(cloud, "read_source_metadata") as read:
            cloud.observe_metadata()
            read.assert_not_called()
