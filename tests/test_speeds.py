"""Writing and movement speed changes must reach the corresponding G-code."""
import unittest

from backend.document import generate_document
from backend.layout import generate
from tests.test_service import FONT


def motion_profile(result):
    profile = dict(drawing=[], travel=[], pen=[])
    down = False
    lines = result['gcode'].splitlines()
    # Header and final origin return deliberately retain the original speed.
    for line in lines[5:-2]:
        feed = float(line.rsplit('F', 1)[1])
        if line.startswith('G1 Z'):
            profile['pen'].append(feed)
            down = line.startswith('G1 Z10 ')
        else:
            profile['drawing' if down else 'travel'].append(feed)
    return profile


class SpeedTests(unittest.TestCase):
    def generate(self, feed, move_feed=None):
        data = dict(text='Hello', feed=feed, seed='speed-test')
        if move_feed is not None:
            data['move_feed'] = move_feed
        return [generate(FONT, data), generate_document(FONT, {**data,'layout_mode':'notebook'})[0]]

    def test_both_speeds_reach_their_own_moves(self):
        for feed, move in ((2400,10000),(10000,2400),(1800,3000),(12000,12000)):
            for result in self.generate(feed,move):
                profile = motion_profile(result)
                self.assertEqual(set(profile['drawing']), {feed})
                self.assertEqual(set(profile['travel']), {move})
                self.assertEqual(set(profile['pen']), {move})
                self.assertEqual(result['metadata']['move_feed'],move)
                self.assertEqual(result['metadata']['commands'],len(result['gcode'].splitlines()))
                self.assertTrue(result['gcode'].endswith('G1 Z0 F3000\nG1 X0 Y0 F3000\n'))

    def test_movement_changes_do_not_change_ink_or_drawing_feed(self):
        for slow,fast in zip(self.generate(2400,3000),self.generate(2400,10000)):
            self.assertEqual(slow['paths'],fast['paths'])
            self.assertEqual(slow['svg'],fast['svg'])
            self.assertEqual(motion_profile(slow)['drawing'],motion_profile(fast)['drawing'])
            self.assertNotEqual(slow['metadata']['sha256'],fast['metadata']['sha256'])
            self.assertEqual(slow['metadata']['strokes'],fast['metadata']['strokes'])

    def test_old_requests_get_current_defaults_and_invalid_speeds_fail(self):
        for result in self.generate(2400):
            self.assertEqual(result['metadata']['move_feed'],10000)
        for bad in (99,12001,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError): self.generate(2400,bad)

    def test_redundant_pen_up_commands_between_strokes_are_removed(self):
        for result in self.generate(2400,10000):
            self.assertNotRegex(result['gcode'],r'G1 Z0 F10000\nG1 Z0 F10000')
            self.assertEqual(result['gcode'].count('G1 Z10 F10000\n'),result['metadata']['strokes'])


if __name__ == '__main__':
    unittest.main()
