# Polityki

Polityka egzekwowana jest kodem (`src/agent_factory/policy.py`) na podstawie `factory/config.yaml`:

- `policy.mode` i broker skutków ubocznych (`authorize_side_effect`): w trybie `local` brak komentarzy, PR i deploy.
- `policy.protected_paths`: zmiana tych plików przez buildera daje `FAIL_POLICY` (chronione wejścia oceny).
- `policy.risk_path_rules` + reguły treści diffu: podnoszą ryzyko; nigdy go nie obniżają.
- Wykrywanie osłabienia testów: skip/xfail, `assert True`, usunięte testy i asercje, usunięte pliki testów.

Zmiana polityki to osobna zmiana kontrolera (operator, testy, review). Zmienia `policy_digest`,
więc wszystkie zależne wyniki trwających jobów stają się nieaktualne.
