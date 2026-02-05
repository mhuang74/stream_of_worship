docker compose -f ../docker/docker-compose.allinone.yml run --rm allinone python poc/poc_analysis_allinone.py
docker compose -f ../docker/docker-compose.yml run --rm librosa python poc/generate_transitions.py
