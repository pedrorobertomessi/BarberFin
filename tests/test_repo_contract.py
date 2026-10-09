"""Contrato de repositório: roda na memória sempre e no Postgres se DATABASE_URL existir.

Postgres: crie um banco VAZIO de teste, aplique schema.sql e rode
  DATABASE_URL=postgresql://user:pass@localhost:5432/teste python -m unittest tests.test_repo_contract
(os testes inserem dados: NUNCA aponte para produção).
"""
import os
import unittest
from datetime import timedelta

from app.audit import verify_chain
from app.domain import NewLedgerEntry
from app.repo.memory import InMemoryRepo
from tests.helpers import T0


def _entry(e2e="E1", source=1, cust=1):
    return NewLedgerEntry(cust, "in", 100, e2e, None, None, None, None, None, T0, source)


class RepoContract:
    def make(self): raise NotImplementedError

    def setUp(self):
        self.repo = self.make()
        self.cust = self.seed_customer()

    def ev(self, key="k1", h="h1", status="received"):
        return self.repo.insert_event(provider="celcoin", entity="e", dedupe_key=key, body_hash=h,
                                      payload_enc=b"x" * 40, status=status, now=T0)

    def test_evento_unico_e_conflito_de_hash(self):
        a = self.ev(); b = self.ev(); c = self.ev(h="outro")
        self.assertEqual((a.created, b.created, b.hash_conflict, c.hash_conflict), (True, False, False, True))
        self.assertEqual(a.event_id, b.event_id)

    def test_claim_com_lease(self):
        self.ev()
        e = self.repo.claim_next_event(now=T0, lease_seconds=60)
        self.assertEqual((e.status, e.attempts), ("processing", 1))
        self.assertIsNone(self.repo.claim_next_event(now=T0 + timedelta(seconds=30), lease_seconds=60))
        again = self.repo.claim_next_event(now=T0 + timedelta(seconds=61), lease_seconds=60)
        self.assertEqual(again.attempts, 2)

    def test_retry_so_apos_a_hora(self):
        self.ev(); e = self.repo.claim_next_event(now=T0, lease_seconds=60)
        self.repo.mark_retry(e.id, T0 + timedelta(seconds=10), "x", T0)
        self.assertIsNone(self.repo.claim_next_event(now=T0 + timedelta(seconds=5), lease_seconds=60))
        self.assertIsNotNone(self.repo.claim_next_event(now=T0 + timedelta(seconds=11), lease_seconds=60))

    def test_livro_unico_por_e2e_e_direcao(self):
        src = self.ev().event_id
        i1, c1 = self.repo.insert_ledger_entry(_entry(source=src, cust=self.cust))
        i2, c2 = self.repo.insert_ledger_entry(_entry(source=src, cust=self.cust))
        self.assertEqual((c1, c2, i1 == i2), (True, False, True))

    def test_outbox_unico(self):
        a = self.repo.enqueue_message(customer_id=self.cust, kind="k", payload={"ledger_id": 1}, dedupe_key="d", now=T0)
        b = self.repo.enqueue_message(customer_id=self.cust, kind="k", payload={"ledger_id": 1}, dedupe_key="d", now=T0)
        self.assertEqual((a[1], b[1], a[0] == b[0]), (True, False, True))
        msgs = self.repo.claim_pending_messages(now=T0, limit=10, lease_seconds=60)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(self.repo.claim_pending_messages(now=T0, limit=10, lease_seconds=60), [])

    def test_transacao_faz_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                self.ev(key="dentro")
                raise RuntimeError("x")
        self.assertTrue(self.ev(key="dentro").created)

    def test_cadeia_de_auditoria(self):
        for i in range(3):
            self.repo.append_audit(ts=T0, action=f"a{i}", subject_type="t", subject_id="1", details={"reason": "r"})
        self.assertTrue(verify_chain(self.repo.list_audit()))


class MemoryContract(RepoContract, unittest.TestCase):
    def make(self): return InMemoryRepo()
    def seed_customer(self): return self.repo.add_customer("acc").id


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "defina DATABASE_URL para testar o Postgres")
class PostgresContract(RepoContract, unittest.TestCase):
    def make(self):
        from app.repo.postgres import PostgresRepo
        repo = PostgresRepo(os.environ["DATABASE_URL"])
        # Limpa só as tabelas mutáveis; audit_log/ledger são append-only (use um banco descartável por execução).
        repo._conn.execute("TRUNCATE outbox_messages, provider_events, customers RESTART IDENTITY CASCADE")
        return repo

    def seed_customer(self):
        return self.repo._conn.execute(
            "INSERT INTO customers(provider_account) VALUES ('acc') RETURNING id").fetchone()["id"]


if __name__ == "__main__":
    unittest.main()
