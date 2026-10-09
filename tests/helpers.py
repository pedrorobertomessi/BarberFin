import base64
import json
from datetime import datetime, timedelta, timezone

from app.crypto import FieldCipher
from app.domain import PixReceipt
from app.providers.fake import FakeBankingProvider
from app.repo.memory import InMemoryRepo
from app.worker import Worker

T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
E2E = "E139358932026020414547uzUUCPfto4"
ACCOUNT = "30023646094934"
PAYER = "FULANO DE TAL BELTRANO"
PAYER_DOC = "98765432100"
LOGIN, PASSWORD = "webhook", "senha-de-teste-0123456789"


def make_cipher() -> FieldCipher:
    return FieldCipher(bytes(range(32)), bytes(range(32, 64)))


def auth_headers(login=LOGIN, password=PASSWORD):
    tok = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def webhook_body(*, e2e=E2E, tx_id="37ae14d3-0000-4000-8000-000000000001", amount="194", account=ACCOUNT,
                 name=PAYER, doc=PAYER_DOC, status="CONFIRMED", entity="pix-payment-in") -> bytes:
    """Monta o payload no formato do doc 'webhook-cash-in' (amount é literal numérico, não string)."""
    d = {"entity": entity, "createTimeStamp": "2026-02-04T11:54:27.2743201", "status": status,
         "body": {"id": tx_id, "amount": "__AMOUNT__", "endToEndId": e2e, "initiationType": "DICT",
                  "transactionIdentification": None,
                  "debitParty": {"name": name, "taxId": doc, "account": "111", "bank": "00000000"},
                  "creditParty": {"account": account, "taxId": "12345678000199", "key": "x@y.com"}}}
    return json.dumps(d).replace('"__AMOUNT__"', amount).encode()


def receipt(e2e=E2E, cents=19400, status="CONFIRMED", account=ACCOUNT) -> PixReceipt:
    return PixReceipt(end_to_end_id=e2e, provider_tx_id="202602040000000001", amount_cents=cents,
                      status=status, credit_account=account, payer_name=PAYER, payer_tax_id=PAYER_DOC)


class World:
    def __init__(self):
        self.repo = InMemoryRepo()
        self.provider = FakeBankingProvider(LOGIN, PASSWORD)
        self.cipher = make_cipher()
        self.worker = Worker(self.repo, self.provider, self.cipher)
        self.customer = self.repo.add_customer(ACCOUNT)
        self.now = T0

    def clock(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)
