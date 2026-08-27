import unittest

from vrchat_monitor.vrchat import presence_fields


class PresenceMappingTests(unittest.TestCase):
    def test_offline_presence_is_explicitly_offline(self):
        result = presence_fields({"status": "join me", "world": "offline", "instance": "offline"})
        self.assertEqual(result["status"], "join me")
        self.assertEqual(result["location"], "offline")

    def test_world_and_instance_are_preserved_as_location(self):
        result = presence_fields({
            "status": "join me",
            "world": "wrld_00000000-0000-0000-0000-000001234567",
            "instance": "123~private(usr_example)~region(jp)",
            "platform": "standalonewindows",
        })
        self.assertEqual(
            result["location"],
            "wrld_00000000-0000-0000-0000-000001234567:123~private(usr_example)~region(jp)",
        )
        self.assertEqual(result["platform"], "standalonewindows")

    def test_active_without_location_is_still_online(self):
        result = presence_fields({"status": "active", "world": "", "instance": ""})
        self.assertEqual(result["location"], "online")

    def test_private_and_traveling_have_stable_special_locations(self):
        self.assertEqual(presence_fields({"status": "active", "world": "private"})["location"], "private")
        self.assertEqual(presence_fields({"status": "active", "instance": "traveling"})["location"], "traveling")


if __name__ == "__main__":
    unittest.main()
