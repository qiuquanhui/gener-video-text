"""Run: python -m unittest discover -s tests -p 'test_*.py'. No network calls."""
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db


class DBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init_db(Path(self.tmp.name) / "test.db")


class AccountTests(DBTestCase):
    def test_create_and_fetch_account(self):
        aid = db.create_account("a@test.com", "hash-1")
        account = db.get_account(aid)
        self.assertEqual(account["email"], "a@test.com")
        self.assertEqual(db.get_account_by_email("a@test.com")["id"], aid)
        self.assertIsNone(db.get_account_by_email("missing@test.com"))

    def test_duplicate_email_rejected(self):
        import sqlite3
        db.create_account("dup@test.com", "hash")
        with self.assertRaises(sqlite3.IntegrityError):
            db.create_account("dup@test.com", "hash-2")

    def test_membership_stacks_on_active_but_not_expired(self):
        aid = db.create_account("vip@test.com", "h")
        first = db.extend_membership(aid, 30)
        self.assertAlmostEqual(first - time.time(), 30 * 86400, delta=10)
        # 未过期时续期：在现有到期时间上叠加
        second = db.extend_membership(aid, 30)
        self.assertAlmostEqual(second - first, 30 * 86400, delta=10)
        # 已过期后续期：从当前时间重新起算
        db.set_membership_expiry(aid, time.time() - 1)
        self.assertFalse(db.is_vip(db.get_account(aid)))
        third = db.extend_membership(aid, 30)
        self.assertAlmostEqual(third - time.time(), 30 * 86400, delta=10)
        self.assertTrue(db.is_vip(db.get_account(aid)))


class SessionTests(DBTestCase):
    def _login(self, email="s@test.com"):
        aid = db.create_account(email, "h")
        db.create_session("tok-1", aid, 60)
        return db.get_session_account("tok-1")

    def test_session_roundtrip_and_logout(self):
        account = self._login()
        self.assertEqual(account["email"], "s@test.com")
        db.delete_session("tok-1")
        self.assertIsNone(db.get_session_account("tok-1"))

    def test_expired_session_invalid(self):
        aid = db.create_account("e@test.com", "h")
        db.create_session("tok-2", aid, -1)
        self.assertIsNone(db.get_session_account("tok-2"))


class OrderTests(DBTestCase):
    def test_mark_order_paid_is_idempotent(self):
        aid = db.create_account("o@test.com", "h")
        db.create_order("ord-1", aid, "monthly", 990, "cny", "cs_test_1")
        order = db.get_order("ord-1")
        self.assertEqual(order["status"], "pending")
        # 并发/重投递下只有第一次 pending→paid 成功
        self.assertTrue(db.mark_order_paid("ord-1"))
        self.assertFalse(db.mark_order_paid("ord-1"))
        self.assertEqual(db.get_order("ord-1")["status"], "paid")
        self.assertIsNotNone(db.get_order("ord-1")["paid_at"])

    def test_pending_order_lookup(self):
        aid = db.create_account("p@test.com", "h")
        db.create_order("ord-2", aid, "monthly", 990, "cny", "cs_test_2")
        found = db.get_pending_order(aid, "monthly")
        self.assertEqual(found["id"], "ord-2")
        db.mark_order_paid("ord-2")
        self.assertIsNone(db.get_pending_order(aid, "monthly"))


class EventAndQuotaTests(DBTestCase):
    def test_record_event_dedupes(self):
        self.assertTrue(db.record_event("evt-1"))
        self.assertFalse(db.record_event("evt-1"))

    def test_quota_increment_accumulates_per_day(self):
        self.assertEqual(db.quota_used("ip:1.2.3.4", "2026-10-26"), 0)
        self.assertEqual(db.quota_increment("ip:1.2.3.4", "2026-10-26"), 1)
        self.assertEqual(db.quota_increment("ip:1.2.3.4", "2026-10-26"), 2)
        self.assertEqual(db.quota_used("ip:1.2.3.4", "2026-10-26"), 2)
        self.assertEqual(db.quota_used("ip:1.2.3.4", "2026-10-27"), 0)


class ActivationCodeTests(DBTestCase):
    def test_platform_code_each_account_once(self):
        a1 = db.create_account("u1@test.com", "h")
        a2 = db.create_account("u2@test.com", "h")
        self.assertTrue(db.redeem_code("VIP2026", a1, 30, single_use=False))
        self.assertFalse(db.redeem_code("VIP2026", a1, 30, single_use=False))  # 同账号二次兑换被拒
        self.assertTrue(db.redeem_code("VIP2026", a2, 30, single_use=False))   # 其他账号仍可
        self.assertTrue(db.is_vip(db.get_account(a1)))

    def test_order_code_is_single_use_globally(self):
        a1 = db.create_account("u3@test.com", "h")
        a2 = db.create_account("u4@test.com", "h")
        code = db.new_activation_code()
        db.add_activation_code(code, 30, order_id="ord-9")
        self.assertEqual(db.get_activation_code(code)["order_id"], "ord-9")
        self.assertTrue(db.redeem_code(code, a1, 30, single_use=True))
        self.assertFalse(db.redeem_code(code, a2, 30, single_use=True))  # 全局第二人兑换被拒

    def test_new_code_format(self):
        code = db.new_activation_code()
        self.assertRegex(code, r"^SD-[A-Z2-9]{6}-[A-Z2-9]{4}$")
        self.assertNotEqual(code, db.new_activation_code())


if __name__ == "__main__":
    unittest.main()
