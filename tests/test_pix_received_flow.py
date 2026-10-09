import logging
import re
import unittest
from datetime import timedelta

from app.audit import verify_chain
from app.channels import RecordingChannel
from app.errors import ProviderNotFound, ProviderRejected, ProviderTemporaryError
from app.ingest import ingest_webhook
from app.messages import TEMPLATES, abbreviate_name, render_pix_received
from app.worker import MAX_EVENT_ATTEMPTS, backoff
from tests.helpers import (ACCOUNT, E2E, PAYER, PAYER_DOC, World, auth_headers, receipt, webhook_body)


class FlowBase(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def ingest(self, raw=None, headers=None, **kw):
        return ingest_webhook(provider=self.w.provider, repo=self.w.repo, cipher=self.w.cipher,
                              headers=auth_headers() if headers is None else headers,
                              raw=webhook_body() if raw is None else raw, now=self.w.clock, **kw)

    def run_all(self):
        n = 0
        while self.w.worker.run_once(self.w.now):
            n += 1
            self.assertLess(n, 50)
        return n

    @property
    def s(self):
        return self.w.repo._s


class HappyPathTests(FlowBase):
    def test_pix_recebido_vira_lancamento_e_mensagem(self):
        self.w.provider.receipts[E2E] = receipt()
        self.assertEqual(self.ingest().outcome, "accepted")
        self.run_all()
        self.assertEqual(len(self.s.ledger), 1)
        entry = next(iter(self.s.ledger.values()))
        self.assertEqual((entry.direction, entry.amount_cents), ("in", 19400))
        ch = RecordingChannel()
        self.assertEqual(self.w.worker.dispatch_outbox(self.w.now, ch), 1)
        self.assertEqual(ch.sent, [(self.w.customer.id, "Entrou um Pix de R$ 194,00 de Fulano B. Foi o quê?")])
        self.assertEqual(self.w.provider.calls, [E2E])           # confirmou na API antes de registrar
        self.assertTrue(verify_chain(self.w.repo.list_audit()))

    def test_nome_e_documento_ficam_cifrados_no_livro(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest(); self.run_all()
        e = next(iter(self.s.ledger.values()))
        self.assertNotIn(PAYER.encode(), e.counterparty_name_enc)
        self.assertNotEqual(e.counterparty_doc_idx, PAYER_DOC)
        self.assertEqual(self.w.cipher.decrypt(e.counterparty_name_enc, b"ledger:name").decode(), PAYER)
        raw_event = next(iter(self.s.events.values())).payload_enc
        self.assertNotIn(PAYER.encode(), raw_event)


class DuplicateTests(FlowBase):
    def test_webhook_duplicado_gera_um_lancamento_e_uma_mensagem(self):
        self.w.provider.receipts[E2E] = receipt()
        self.assertEqual(self.ingest().outcome, "accepted")
        self.assertEqual(self.ingest().outcome, "duplicate")
        self.run_all()
        self.assertEqual((len(self.s.events), len(self.s.ledger), len(self.s.outbox)), (1, 1, 1))

    def test_mesmo_pix_com_ids_de_evento_diferentes_nao_duplica_o_livro(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest(raw=webhook_body(tx_id="aaaa-1"))
        self.ingest(raw=webhook_body(tx_id="bbbb-2"))
        self.assertEqual(len(self.s.events), 2)
        self.run_all()
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (1, 1))

    def test_mesma_chave_corpo_diferente_gera_auditoria(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest(raw=webhook_body(name="A"))
        self.assertEqual(self.ingest(raw=webhook_body(name="B")).outcome, "duplicate")
        self.assertIn("webhook_conflict", [a.action for a in self.w.repo.list_audit()])

    def test_reprocessar_evento_ja_processado_e_idempotente(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest(); self.run_all()
        ev = next(iter(self.s.events.values()))
        self.w.worker._process(ev, self.w.now)                  # simula reprocesso manual
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (1, 1))

    def test_dispatch_duas_vezes_nao_reenvia(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest(); self.run_all()
        ch = RecordingChannel()
        self.w.worker.dispatch_outbox(self.w.now, ch)
        self.w.worker.dispatch_outbox(self.w.now, ch)
        self.assertEqual(len(ch.sent), 1)


class SecurityTests(FlowBase):
    def test_sem_credencial_ou_errada_nada_e_gravado(self):
        for h in ({}, auth_headers("webhook", "senha-errada-xxxxxxxxxxx")):
            self.assertEqual(self.ingest(headers=h).http_status, 401)
        self.assertEqual(len(self.s.events), 0)
        self.assertEqual([a.action for a in self.w.repo.list_audit()], ["webhook_auth_failed"] * 2)

    def test_corpo_grande_demais(self):
        self.assertEqual(self.ingest(raw=b"x" * 2000, max_bytes=1000).http_status, 413)
        self.assertEqual(len(self.s.events), 0)

    def test_payload_autenticado_mas_invalido_vai_para_quarentena_com_200(self):
        r = self.ingest(raw=b"{lixo")
        self.assertEqual((r.http_status, r.outcome), (200, "invalid"))
        self.assertEqual(next(iter(self.s.events.values())).status, "quarantined")

    def test_webhook_forjado_sem_pix_real_nao_notifica_ninguem(self):
        # autenticado, mas a consulta não encontra o Pix: tenta até o limite e quarentena
        self.ingest()
        self.run_all_with_time()
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (0, 0))
        ev = next(iter(self.s.events.values()))
        self.assertEqual(ev.status, "quarantined")
        self.assertTrue(ev.last_error.startswith("max_attempts"))
        self.assertEqual(ev.attempts, MAX_EVENT_ATTEMPTS)

    def run_all_with_time(self):
        for _ in range(MAX_EVENT_ATTEMPTS + 2):
            self.w.worker.run_once(self.w.now)
            self.w.advance(minutes=20)

    def test_divergencia_de_valor_vai_para_quarentena(self):
        self.w.provider.receipts[E2E] = receipt(cents=100)       # webhook diz R$ 194, API diz R$ 1
        self.ingest(); self.run_all()
        ev = next(iter(self.s.events.values()))
        self.assertEqual((ev.status, ev.last_error), ("quarantined", "amount_mismatch"))
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (0, 0))

    def test_divergencia_de_conta_e_de_e2e(self):
        self.w.provider.receipts[E2E] = receipt(account="999")
        self.ingest(); self.run_all()
        self.assertEqual(next(iter(self.s.events.values())).last_error, "account_mismatch")

    def test_conta_desconhecida_vai_para_quarentena_sem_consultar_api(self):
        self.ingest(raw=webhook_body(account="000"))
        self.run_all()
        ev = next(iter(self.s.events.values()))
        self.assertEqual((ev.status, ev.last_error), ("quarantined", "unknown_account"))
        self.assertEqual(self.w.provider.calls, [])


class ProviderErrorTests(FlowBase):
    def test_erro_temporario_reagenda_com_backoff_e_depois_conclui(self):
        self.w.provider.scripted.extend([ProviderTemporaryError("x"), ProviderTemporaryError("x")])
        self.w.provider.receipts[E2E] = receipt()
        self.ingest()
        self.assertTrue(self.w.worker.run_once(self.w.now))
        ev = next(iter(self.s.events.values()))
        self.assertEqual((ev.status, ev.last_error), ("retry", "ProviderTemporaryError"))
        self.assertEqual(ev.next_attempt_at, self.w.now + backoff(1))
        self.assertFalse(self.w.worker.run_once(self.w.now))     # ainda não é hora
        self.w.advance(seconds=11); self.assertTrue(self.w.worker.run_once(self.w.now))
        self.w.advance(seconds=21); self.assertTrue(self.w.worker.run_once(self.w.now))
        self.assertEqual(next(iter(self.s.events.values())).status, "processed")
        self.assertEqual(len(self.s.ledger), 1)

    def test_erro_definitivo_do_provedor_tambem_nao_registra(self):
        self.w.provider.scripted.append(ProviderRejected("401"))
        self.ingest(); self.run_all()
        self.assertEqual(len(self.s.ledger), 0)

    def test_pendente_no_provedor_nao_notifica_ate_confirmar(self):
        self.w.provider.scripted.append(receipt(status="PENDING"))
        self.w.provider.receipts[E2E] = receipt()
        self.ingest()
        self.w.worker.run_once(self.w.now)
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (0, 0))
        self.w.advance(seconds=30); self.w.worker.run_once(self.w.now)
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (1, 1))

    def test_falha_no_meio_da_transacao_nao_deixa_estado_parcial(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest()
        original = self.w.repo.enqueue_message
        def boom(**kw): raise RuntimeError("queda")
        self.w.repo.enqueue_message = boom
        with self.assertRaises(RuntimeError):
            self.w.worker.run_once(self.w.now)
        self.w.repo.enqueue_message = original
        self.assertEqual(len(self.s.ledger), 0)                  # rollback do lançamento
        self.w.advance(minutes=2)                                # lease expira -> outro worker reassume
        self.assertTrue(self.w.worker.run_once(self.w.now))
        self.assertEqual((len(self.s.ledger), len(self.s.outbox)), (1, 1))

    def test_falha_no_envio_da_mensagem_reagenda(self):
        self.w.provider.receipts[E2E] = receipt()
        self.ingest(); self.run_all()
        ch = RecordingChannel(); ch.fail_next = 1
        self.assertEqual(self.w.worker.dispatch_outbox(self.w.now, ch), 0)
        msg = next(iter(self.s.outbox.values()))
        self.assertEqual(msg.status, "pending")
        self.w.advance(seconds=30)
        self.assertEqual(self.w.worker.dispatch_outbox(self.w.now, ch), 1)


class PrivacyAndTextTests(FlowBase):
    def test_logs_sem_dados_pessoais_ou_financeiros(self):
        class Capture(logging.Handler):
            def __init__(self): super().__init__(); self.lines = []
            def emit(self, record): self.lines.append(record.getMessage())
        cap = Capture(); root = logging.getLogger(); root.addHandler(cap); old = root.level; root.setLevel(logging.DEBUG)
        try:
            self.w.provider.scripted.append(ProviderTemporaryError("x"))
            self.w.provider.receipts[E2E] = receipt()
            self.ingest(); self.ingest(headers={})
            self.w.worker.run_once(self.w.now); self.w.advance(minutes=1); self.run_all()
            self.w.worker.dispatch_outbox(self.w.now, RecordingChannel())
        finally:
            root.removeHandler(cap); root.setLevel(old)
        text = "\n".join(cap.lines)
        self.assertTrue(text)                                    # houve log
        for secret in ("FULANO", "Fulano", PAYER_DOC, ACCOUNT, E2E, "194", "19400", "R$"):
            self.assertNotIn(secret, text)

    def test_auditoria_rejeita_dados_pessoais(self):
        for details in ({"name": "x"}, {"amount_cents": 1}, {"reason": ["lista"]}):
            with self.assertRaises(ValueError):
                self.w.repo.append_audit(ts=self.w.now, action="x", subject_type="t", subject_id="1", details=details)

    def test_nomes_abreviados(self):
        self.assertEqual(abbreviate_name("MARIA DA SILVA SANTOS"), "Maria S.")
        self.assertEqual(abbreviate_name("joão"), "João")
        self.assertEqual(abbreviate_name(None), "um pagador")
        self.assertEqual(abbreviate_name("  "), "um pagador")

    def test_pontuacao_da_mensagem(self):
        self.assertEqual(render_pix_received(4500, "Ana Lima"), "Entrou um Pix de R$ 45,00 de Ana L. Foi o quê?")
        self.assertEqual(render_pix_received(4500, "Ana"), "Entrou um Pix de R$ 45,00 de Ana. Foi o quê?")
        self.assertEqual(render_pix_received(4500, None), "Entrou um Pix de R$ 45,00 de um pagador. Foi o quê?")

    def test_templates_nao_usam_termos_proibidos(self):
        for text in list(TEMPLATES.values()) + [render_pix_received(4500, "Ana Lima")]:
            self.assertIsNone(re.search(r"\bbanc[oa]s?\b|\bbank\w*", text, re.I), text)


if __name__ == "__main__":
    unittest.main()
