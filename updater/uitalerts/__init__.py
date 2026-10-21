"""Mail alerts for uitjes: a visitor saves a filter view (search words, city,
categories), confirms it from their inbox, and is mailed when a published build
brings a new event that matches - and, if they asked, again a few days before it.

Runs as its own container from the uitjes-updater image (`python alerts.py
serve`): a small JSON API behind Traefik on uitjes.<domain>/api/alerts/, plus a
background loop that watches the published events.json. See alerts.py.
"""
