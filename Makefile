PY=PYTHONPATH=src python

recv:
	$(PY) tools/uf1_recv_udp.py --seconds 20

send:
	$(PY) tools/uf1_send_udp.py --seconds 20 --host 127.0.0.1