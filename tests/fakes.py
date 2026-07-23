"""common.py testleri icin gercek bir tarayici acmadan Playwright
Page/BrowserContext nesnelerinin yerine gecen minimal sahte siniflar.

Bu dosya "test_*.py" desenine UYMADIGI icin pytest tarafindan bir test
modulu olarak DEGIL, sadece testlerin ice aktardigi sirdan bir yardimci
modul olarak islenir.
"""


class FakePage:
    """Playwright Page nesnesinin sahte yerine gecicisi.

    live_url_value/live_url_exc: page.evaluate() cagrisinin (canli JS
    durumu) sonucunu ya da firlatacagi hatayi simule eder.
    cached_url/cached_url_exc: page.url ozelliginin (Playwright'in
    ONBELLEKLEDIGI, SSO capraz-kaynak yonlendirmesinde TAKILI KALABILEN
    deger - bkz. common.py::live_url docstring) sonucunu simule eder.
    wait_exc: wait_for_timeout() cagrisinin firlatacagi hatayi simule
    eder (ör. tarayici kapandiginda gercekte olan).
    """

    def __init__(
        self,
        live_url_value: str | None = None,
        live_url_exc: Exception | None = None,
        cached_url: str = "",
        cached_url_exc: Exception | None = None,
        wait_exc: Exception | None = None,
    ) -> None:
        self._live_url_value = live_url_value
        self._live_url_exc = live_url_exc
        self._cached_url = cached_url
        self._cached_url_exc = cached_url_exc
        self._wait_exc = wait_exc

    def evaluate(self, _script: str) -> str | None:
        if self._live_url_exc:
            raise self._live_url_exc
        return self._live_url_value

    @property
    def url(self) -> str:
        if self._cached_url_exc:
            raise self._cached_url_exc
        return self._cached_url

    def wait_for_timeout(self, _ms: int) -> None:
        if self._wait_exc:
            raise self._wait_exc


class FakeContext:
    """Playwright BrowserContext'in sahte yerine gecicisi - sadece
    `resolve_active_page`/`find_blackboard_pages`'in kullandigi
    `.pages` listesini tasir."""

    def __init__(self, pages: list) -> None:
        self.pages = pages


class FakeTitledPage:
    """Sadece derive_course_label() testleri icin - page.title() disinda
    hicbir seye ihtiyaci yok."""

    def __init__(self, title: str) -> None:
        self._title = title

    def title(self) -> str:
        return self._title
