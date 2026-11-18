import io
import sqlite3
import unittest
from dataclasses import dataclass
from types import SimpleNamespace as N

from PIL import Image
from profile_card import ProfileStore, render_card, target_for, COLORS


@dataclass(order=True)
class Role:
    position: int
    id: int


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.store = ProfileStore(self.db)

    def tearDown(self):
        self.db.close()

    def test_optional_user_defaults_to_caller(self):
        caller, other = N(id=1), N(id=2)
        interaction = N(user=caller)
        self.assertIs(target_for(interaction,None),caller)
        self.assertIs(target_for(interaction,other),other)

    def test_actual_role_order_not_category_order(self):
        self.store.assign(1,'서버장',10)
        self.store.assign(1,'총관리자',20)
        self.assertEqual(self.store.category(1,[Role(1,10),Role(9,20)]),'총관리자')
        self.assertEqual(self.store.category(1,[Role(10,10),Role(9,20)]),'서버장')
        self.assertEqual(self.store.category(2,[Role(1,10)]),'서버원')
        self.assertEqual(self.store.category(1,[]),'서버원')

    def test_reassign_and_permit_isolation(self):
        self.store.assign(1,'서버장',10)
        self.store.assign(1,'총관리자',10)
        self.assertEqual(self.store.category(1,[Role(1,10)]),'총관리자')
        self.assertFalse(self.store.permit(1,5))
        self.store.set_permit(1,5,True)
        self.assertTrue(self.store.permit(1,5))
        self.assertFalse(self.store.permit(2,5))
        self.assertFalse(self.store.permit(1,6))
        self.store.set_permit(1,5,False)
        self.assertFalse(self.store.permit(1,5))

    def test_every_color_renders_at_correct_ratio(self):
        avatar = io.BytesIO()
        Image.new('RGB',(100,200),'#ff00ff').save(avatar,'PNG')
        for category in COLORS:
            card = render_card(avatar.getvalue(),'아주 긴 이름'*12,'서버 이름'*12,
                               category,0,0,False,usable_seconds=0)
            with Image.open(io.BytesIO(card)) as image:
                self.assertEqual(image.size,(1500,900))
                self.assertEqual(image.getpixel((100,200)),(255,0,255))
                self.assertEqual(image.getpixel((1000,680)),(217,55,66))
        card = render_card(avatar.getvalue(),'유저','서버','서버장',55800,2,True,usable_seconds=37800)
        with Image.open(io.BytesIO(card)) as image:
            self.assertEqual(image.getpixel((1000,680)),(7,155,53))


if __name__ == '__main__':
    unittest.main()
