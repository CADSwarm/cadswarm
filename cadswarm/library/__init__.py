"""The team's components library: uploaded parts, built-in standard parts, and parts imported from a link.

store.py     files under <project>/library/<id>/ and their metadata
standard.py  the built-in catalog (standard_parts.json) and hole sizes for fits
search.py    ranking parts against a query and dimension constraints
recommend.py which parts suit a need, and why
suggest.py   the team settings a part implies (fits, hole classes, printing)
fetch.py     downloading a part file from a direct link
pack.py      exporting / importing the library as a .zip pack

Stdlib only: the host's helper may run with FreeCAD's Python.
"""
