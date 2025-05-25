# party_bot/cogs/party_leader_actions.py

import disnake
# Usunięto 'from .. import config' - komendy w PartyManagementCog będą miały dostęp do config.
# Funkcje tutaj będą wywoływane przez metody PartyManagementCog.

class LeaderControlPanelView(disnake.ui.View):
    def __init__(self, party_id: int):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(label="Rozwiąż Party", style=disnake.ButtonStyle.danger, custom_id=f"leader_disband_{party_id}"))