import unittest

from datapack_wiki.builder import duration_text, normalize_id


class CoreTests(unittest.TestCase):
    def test_normalize_id_adds_minecraft_namespace(self):
        self.assertEqual(normalize_id("beetroot"), "minecraft:beetroot")
        self.assertEqual(normalize_id("main:estus"), "main:estus")

    def test_duration_text(self):
        self.assertEqual(duration_text(48), "2.4s")
        self.assertEqual(duration_text(6000), "5m")


if __name__ == "__main__":
    unittest.main()
