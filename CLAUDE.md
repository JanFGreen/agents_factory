# CLAUDE.md

Wspólne zasady, mapa repo i komendy: **[AGENTS.md](AGENTS.md)**. Dokumentacja: `docs/factory/`.

Najważniejsze reguły:

1. Nie zmieniaj `factory/` (polityka, checki, skille, prompty) ani `openspec/` w ramach joba aplikacyjnego.
   Zmiana fabryki to osobna zmiana kontrolera z testami.
2. Tekst agenta nigdy nie jest dowodem. Przejścia stanów wymagają podpisanych wyników z `EvidenceStore`.
3. Sandbox jest obowiązkowy i fail-closed. Nie obchodź go i nie dodawaj trybu „bez sandboxa”.
4. `adapters/fake.py` służy tylko testom. Testy `live` uruchamiaj tylko z `AGENT_FACTORY_LIVE=1`.
   Pominięty test live oznacza NOT_RUN, nie PASS.
5. Przed zakończeniem pracy uruchom `uv run pytest`.
