"""Canonical registry of fine-grained action keys (R12.01 / R3.11).

Each entry is (key, label). ``key`` matches ``accounts.ActionPermission.action_key``;
``label`` is the pt-BR description shown in admin screens.
"""

ACTIONS = [
    ('customers.delete', 'Excluir clientes'),
    ('catalog.delete', 'Excluir categorias e produtos'),
    ('rentals.delete', 'Excluir locações'),
    ('rentals.cancel', 'Cancelar locações'),
    ('billing.receive', 'Registrar recebimentos'),
    ('billing.reverse', 'Estornar pagamentos'),
    ('billing.cash', 'Lançar movimentos manuais de caixa'),
    ('reports.export', 'Exportar relatórios (CSV)'),
]

ACTION_KEYS = [key for key, _ in ACTIONS]
ACTION_LABELS = dict(ACTIONS)
