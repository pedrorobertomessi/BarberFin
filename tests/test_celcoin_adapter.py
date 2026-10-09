import base64
import json
import unittest
from datetime import datetime

from app.errors import (ParseError, ProviderNotFound, ProviderRejected, ProviderTemporaryError)
from app.providers.celcoin import (BasicWebhookAuth, CelcoinClient, HttpResponse, e2e_date,
                                   parse_celcoin_webhook, parse_provider_timestamp)
from tests.helpers import E2E, webhook_body


class ParserTests(unittest.TestCase):
    def test_pix_in_valido(self):
        ev = parse_celcoin_webhook(webhook_body(amount="194"))
        self.assertEqual(ev.entity, "pix-payment-in")
        self.assertEqual(ev.receipt.amount_cents, 19400)
        self.assertEqual(ev.receipt.end_to_end_id, E2E)
        self.assertEqual(ev.receipt.credit_account, "30023646094934")

    def test_valor_com_centavos(self):
        self.assertEqual(parse_celcoin_webhook(webhook_body(amount="45.50")).receipt.amount_cents, 4550)

    def test_amount_invalido(self):
        for amount in ("0", "-5", "10.001", '"abc"', "null"):
            with self.assertRaises(ParseError, msg=amount):
                parse_celcoin_webhook(webhook_body(amount=amount))

    def test_campos_obrigatorios(self):
        for raw in (b"nao e json", b"[]", b'{"status":"CONFIRMED"}',
                    json.dumps({"entity": "pix-payment-in", "status": "CONFIRMED", "body": {}}).encode()):
            with self.assertRaises(ParseError):
                parse_celcoin_webhook(raw)

    def test_entidade_nao_tratada_nao_quebra(self):
        ev = parse_celcoin_webhook(webhook_body(entity="pix-payment-out"))
        self.assertIsNone(ev.receipt)
        self.assertTrue(ev.dedupe_key.startswith("pix-payment-out|"))

    def test_dedupe_key_inclui_status(self):
        a = parse_celcoin_webhook(webhook_body(status="CONFIRMED"))
        b = parse_celcoin_webhook(webhook_body(status="PENDING"))
        self.assertNotEqual(a.dedupe_key, b.dedupe_key)

    def test_datas(self):
        self.assertEqual(e2e_date("E30944783202603111754035824df3da"), "20260311")
        self.assertIsNone(e2e_date("curto"))
        # a consulta traz MM/dd/aaaa: '03/11/2026' = 11 de março, igual ao E2E acima
        self.assertEqual(parse_provider_timestamp("03/11/2026 17:54:08"), datetime(2026, 3, 11, 17, 54, 8))
        self.assertEqual(parse_provider_timestamp("2026-02-04T11:54:27.2743201"),
                         datetime(2026, 2, 4, 11, 54, 27, 274320))
        self.assertIsNone(parse_provider_timestamp("lixo"))


class WebhookAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = BasicWebhookAuth("login", "senha-bem-longa-12345")

    def _h(self, cred):
        return {"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()}

    def test_ok_e_case_insensitive(self):
        self.assertTrue(self.auth.check(self._h("login:senha-bem-longa-12345")))
        h = {"authorization": self._h("login:senha-bem-longa-12345")["Authorization"]}
        self.assertTrue(self.auth.check(h))

    def test_recusa(self):
        for h in ({}, self._h("login:errada"), self._h("outro:senha-bem-longa-12345"),
                  {"Authorization": "Bearer abc"}, {"Authorization": "Basic %%%"}, {"Authorization": "Basic"}):
            self.assertFalse(self.auth.check(h))

    def test_senha_curta_e_recusada_na_configuracao(self):
        with self.assertRaises(ValueError):
            BasicWebhookAuth("a", "curta")


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


TOKEN = HttpResponse(200, json.dumps({"access_token": "tok", "expires_in": 2400, "token_type": "bearer"}).encode())
CONSULTA = HttpResponse(200, json.dumps({"requestBody": {
    "endToEndId": E2E, "amount": 0.66, "status": "CONFIRMED", "transactionId": 202603110000000531,
    "creditParty": {"account": "30023646094934"}, "debitParty": {"name": "X Y", "taxId": "1"},
    "createTimestamp": "03/11/2026 17:54:08"}}).replace("0.66", "0.66").encode())


class ClientTests(unittest.TestCase):
    def client(self, *resp):
        self.http = FakeHttp(*resp)
        return CelcoinClient(self.http, "https://sandbox.example", "id", "secret")

    def test_consulta_ok(self):
        r = self.client(TOKEN, CONSULTA).fetch_pix_receipt(end_to_end_id=E2E)
        self.assertEqual((r.amount_cents, r.status), (66, "CONFIRMED"))
        self.assertEqual(self.http.calls[1][2]["params"], {"endtoEnd": E2E})

    def test_token_em_cache(self):
        c = self.client(TOKEN, CONSULTA, CONSULTA)
        c.fetch_pix_receipt(end_to_end_id=E2E)
        c.fetch_pix_receipt(end_to_end_id=E2E)
        self.assertEqual(sum(1 for m, u, _ in self.http.calls if u.endswith("/v5/token")), 1)

    def test_401_renova_token_uma_vez(self):
        c = self.client(TOKEN, HttpResponse(401, b""), TOKEN, CONSULTA)
        self.assertEqual(c.fetch_pix_receipt(end_to_end_id=E2E).amount_cents, 66)

    def test_erros(self):
        with self.assertRaises(ProviderNotFound):
            self.client(TOKEN, HttpResponse(404, b"")).fetch_pix_receipt(end_to_end_id=E2E)
        with self.assertRaises(ProviderTemporaryError):
            self.client(TOKEN, HttpResponse(503, b"")).fetch_pix_receipt(end_to_end_id=E2E)
        with self.assertRaises(ProviderTemporaryError):
            self.client(TOKEN, HttpResponse(429, b"")).fetch_pix_receipt(end_to_end_id=E2E)
        with self.assertRaises(ProviderTemporaryError):
            self.client(TOKEN, TimeoutError()).fetch_pix_receipt(end_to_end_id=E2E)
        with self.assertRaises(ProviderRejected):
            self.client(HttpResponse(401, b"")).fetch_pix_receipt(end_to_end_id=E2E)
        with self.assertRaises(ProviderNotFound):
            self.client(TOKEN, HttpResponse(200, b'{"requestBody": null}')).fetch_pix_receipt(end_to_end_id=E2E)


if __name__ == "__main__":
    unittest.main()
