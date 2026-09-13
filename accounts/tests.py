from django.test import TestCase
from django.db import IntegrityError

from accounts.models import User
from accounts.utils import (
    normalize_otp,
    generate_recovery_otp,
    hash_recovery_otp,
    verify_recovery_otp,
    generate_secure_password,
    OTP_CHARACTERS,
)


class UserModelTests(TestCase):
    def test_user_creation_with_email_and_hash(self):
        user = User.objects.create_user(
            username="ops_admin",
            email="admin@personalops.com",
            password="SecurePassword2026!"
        )
        self.assertEqual(user.username, "ops_admin")
        self.assertEqual(user.email, "admin@personalops.com")
        self.assertTrue(user.check_password("SecurePassword2026!"))
        self.assertFalse(user.check_password("WrongPassword"))

    def test_user_email_uniqueness(self):
        User.objects.create_user(username="user1", email="shared@personalops.com", password="Pass1!")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(username="user2", email="shared@personalops.com", password="Pass2!")


class RecoveryOTPUtilsTests(TestCase):
    def test_generate_recovery_otp_format(self):
        otp = generate_recovery_otp()
        self.assertTrue(otp.startswith("PO-"))
        parts = otp.split("-")
        self.assertEqual(len(parts), 4)  # PO, XXXX, XXXX, XXXX
        self.assertEqual(len(parts[1]), 4)
        self.assertEqual(len(parts[2]), 4)
        self.assertEqual(len(parts[3]), 4)
        # Verify characters are from allowed set
        for part in parts[1:]:
            for char in part:
                self.assertIn(char, OTP_CHARACTERS)

    def test_normalize_otp(self):
        self.assertEqual(normalize_otp("PO-8F2K-M3NP-X94W"), "8F2KM3NPX94W")
        self.assertEqual(normalize_otp("po-8f2k-m3np-x94w"), "8F2KM3NPX94W")
        self.assertEqual(normalize_otp("  8F2K M3NP X94W  "), "8F2KM3NPX94W")
        self.assertEqual(normalize_otp(""), "")
        self.assertEqual(normalize_otp(None), "")

    def test_hash_and_verify_recovery_otp(self):
        raw_otp = generate_recovery_otp()
        hashed = hash_recovery_otp(raw_otp)

        # Correct variations should verify
        self.assertTrue(verify_recovery_otp(raw_otp, hashed))
        self.assertTrue(verify_recovery_otp(raw_otp.lower(), hashed))
        self.assertTrue(verify_recovery_otp(normalize_otp(raw_otp), hashed))

        # Wrong OTP should fail
        self.assertFalse(verify_recovery_otp("PO-0000-0000-0000", hashed))
        self.assertFalse(verify_recovery_otp("", hashed))
        self.assertFalse(verify_recovery_otp(raw_otp, ""))

    def test_verify_recovery_otp_legacy_fallback(self):
        # Fallback to direct plaintext match if legacy unhashed OTP is stored
        raw_otp = "PO-AAAA-BBBB-CCCC"
        self.assertTrue(verify_recovery_otp("PO-aaaa-bbbb-cccc", raw_otp))
        self.assertFalse(verify_recovery_otp("PO-XXXX-YYYY-ZZZZ", raw_otp))

    def test_generate_secure_password(self):
        pwd = generate_secure_password(length=20)
        self.assertEqual(len(pwd), 20)
        self.assertTrue(any(c.isalpha() for c in pwd))
        self.assertTrue(any(c.isdigit() or c in "!@#$%^&*" for c in pwd))
