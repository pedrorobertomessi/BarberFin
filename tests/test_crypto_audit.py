import unittest
from dataclasses import replace

from app.audit import compute_hash, verify_chain
from app.crypto import CryptoError, FieldCipher
from tests.helpers import World, make_cipher


class CryptoTests(unittest.TestCase):
    def test_roundtrip_e_adulteracao(self):
        c = make_cipher()
        blob = c.encrypt(b"segredo", b"aad")
        self.assertEqual(c.decrypt(blob, b"aad"), b"segredo")
        with self.assertRaises(CryptoError): c.decrypt(blob, b"outro-aad")
        with self.assertRaises(CryptoError): c.decrypt(blob[:-1] + bytes([blob[-1] ^ 1]), b"aad")
        with self.assertRaises(CryptoError): c.decrypt(b"curto")

    def test_nonce_diferente_a_cada_cifra(self):
        c = make_cipher()
        self.assertNotEqual(c.encrypt(b"x"), c.encrypt(b"x"))

    def test_indice_cego_deterministico_e_normalizado(self):
        c = make_cipher()
        self.assertEqual(c.blind_index("123.456.789-00"), c.blind_index("12345678900"))
        self.assertNotEqual(c.blind_index("1"), c.blind_index("2"))

    def test_chaves_invalidas(self):
        with self.assertRaises(CryptoError): FieldCipher(b"a" * 31, b"b" * 32)
        with self.assertRaises(CryptoError): FieldCipher(b"a" * 32, b"a" * 32)


class AuditTests(unittest.TestCase):
    def test_cadeia_valida_e_adulteracao_detectada(self):
        w = World()
        for i in range(3):
            w.repo.append_audit(ts=w.now, action=f"a{i}", subject_type="t", subject_id=str(i), details={"reason": "x"})
        entries = w.repo.list_audit()
        self.assertTrue(verify_chain(entries))
        tampered = list(entries); tampered[1] = replace(tampered[1], action="forjado")
        self.assertFalse(verify_chain(tampered))
        self.assertFalse(verify_chain([entries[0], entries[2]]))     # entrada removida
        self.assertTrue(verify_chain([]))

    def test_ts_sem_fuso_e_recusado(self):
        from datetime import datetime
        with self.assertRaises(ValueError):
            compute_hash("0" * 64, datetime(2026, 1, 1), "a", "t", "1", {})


if __name__ == "__main__":
    unittest.main()
