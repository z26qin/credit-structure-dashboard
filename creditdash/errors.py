"""Errors the CLI and web app turn into messages. `exit_code` is what `creditdash build` returns."""


class CreditdashError(Exception):
    exit_code = 1


class ConfigError(CreditdashError):
    exit_code = 1


class UnknownTicker(CreditdashError):
    exit_code = 2


class UnsupportedFiler(CreditdashError):
    exit_code = 3


class SecError(CreditdashError):
    exit_code = 4


class LlmError(CreditdashError):
    exit_code = 5
