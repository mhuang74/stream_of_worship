docker compose -f ../docker/docker-compose.allinone.yml run --rm allinone python poc/poc_analysis_allinone.py
uv run python analyze_sections.py
