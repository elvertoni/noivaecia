"""Central registry of access-controlled modules.

Each entry is a (key, label) pair. ``key`` is stored on
``accounts.ModulePermission.module_key`` and declared on protected views via
``ModuleAccessMixin.module_key``; ``label`` is the Brazilian-Portuguese name
shown in management screens.
"""

MODULES = [
    ('customers', 'Clientes'),
    ('catalog', 'Catálogo'),
    ('company', 'Empresa'),
    ('rentals', 'Locações'),
    ('movements', 'Movimentação'),
    ('billing', 'Financeiro'),
    ('reports', 'Relatórios'),
    ('maintenance', 'Manutenção'),
]

MODULE_KEYS = [key for key, _ in MODULES]

MODULE_LABELS = dict(MODULES)
