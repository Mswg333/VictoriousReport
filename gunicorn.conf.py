"""Podesavanja servera (gunicorn ih cita sam iz ovog fajla).

Vise niti: dok jedna strana ceka platformu, ostale strane i korisnici rade.
"""
worker_class = "gthread"
workers = 1
threads = 8
timeout = 120
