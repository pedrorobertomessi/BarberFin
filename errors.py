"""Erros de domínio. Mensagens aqui NUNCA devem conter dados pessoais ou valores."""


class AppError(Exception):
    """Erro base."""


class ParseError(AppError):
    """Payload do provedor inválido ou incompleto."""


class MoneyError(ValueError):
    """Valor monetário inválido."""


class ProviderError(AppError):
    retriable: bool = False


class ProviderTemporaryError(ProviderError):
    """Falha transitória (rede, 5xx, 429)."""
    retriable = True


class ProviderNotFound(ProviderError):
    """Recurso ainda não visível no provedor (pode ser atraso ou evento forjado)."""
    retriable = True


class ProviderRejected(ProviderError):
    """Erro definitivo (4xx de credencial/validação)."""
    retriable = False
