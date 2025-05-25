# party_bot/cogs/party_join_logic.py

import disnake
from disnake.ext import commands


class JoinRequestApprovalView(disnake.ui.View):
    def __init__(self, party_id: int, requesting_user_id: int, bot_instance: disnake.Client,
                 party_management_cog: commands.Cog):
        super().__init__(timeout=12 * 60 * 60)
        self.party_id = party_id
        self.requesting_user_id = requesting_user_id
        self.bot = bot_instance
        self.party_cog = party_management_cog
        self.decision_made = False

    async def handle_decision(self, interaction: disnake.MessageInteraction, accepted: bool):
        # Defer zostało już wywołane w accept_join/reject_join
        if self.decision_made:
            await interaction.followup.send("Decyzja została już podjęta.", ephemeral=True)
            return
        self.decision_made = True

        from .party_manager import active_parties, save_party_data

        party_data = active_parties.get(self.party_id)

        if not party_data or interaction.user.id != party_data["leader_id"]:
            await interaction.followup.send("Tylko lider tego party może zaakceptować lub odrzucić prośbę.",
                                            ephemeral=True)
            self.decision_made = False
            return

        try:
            await interaction.message.delete()
        except disnake.HTTPException:
            pass

        requesting_user = self.bot.get_user(self.requesting_user_id)
        if not requesting_user:
            try:
                requesting_user = await self.bot.fetch_user(self.requesting_user_id)
            except disnake.NotFound:
                await interaction.followup.send(
                    f"Nie można odnaleźć użytkownika o ID {self.requesting_user_id}. Prośba anulowana.", ephemeral=True)
                if party_data and self.requesting_user_id in party_data.get("pending_join_requests", []):
                    party_data["pending_join_requests"].remove(self.requesting_user_id)
                    save_party_data()
                self.stop()
                return
            except disnake.HTTPException as e:
                await interaction.followup.send(
                    f"Wystąpił błąd sieciowy przy próbie pobrania danych użytkownika: {e}", ephemeral=True)
                self.stop()
                return

        if party_data and self.requesting_user_id in party_data.get("pending_join_requests", []):
            party_data["pending_join_requests"].remove(self.requesting_user_id)
            if not accepted:
                save_party_data()

        if accepted:
            if self.requesting_user_id in party_data.get("member_ids", []):
                await interaction.followup.send(f"{requesting_user.mention} jest już członkiem tego party.",
                                                ephemeral=True)
                self.stop()
                return

            guild = self.bot.get_guild(party_data["guild_id"])
            if not guild:
                await interaction.followup.send("Błąd: Serwer, na którym utworzono party, jest nieosiągalny.",
                                                ephemeral=True)
                self.stop()
                return

            member_object = guild.get_member(self.requesting_user_id)
            if not member_object:
                try:
                    member_object = await guild.fetch_member(self.requesting_user_id)
                except disnake.NotFound:
                    await interaction.followup.send(
                        f"Nie można odnaleźć użytkownika {requesting_user.mention} na serwerze. "
                        f"Mógł opuścić serwer przed akceptacją.", ephemeral=True)
                    save_party_data()  # Zapisz usunięcie z pending, bo użytkownika nie ma na serwerze
                    self.stop()
                    return
                except disnake.HTTPException as e:
                    await interaction.followup.send(
                        f"Wystąpił błąd sieciowy przy próbie pobrania danych członka z serwera: {e}", ephemeral=True)
                    self.stop()
                    return

            try:
                category_id = party_data.get("category_id")
                category_obj = guild.get_channel(category_id) if category_id else None

                if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                    cat_perms = disnake.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True,
                                                            connect=True, speak=True, stream=True,
                                                            use_voice_activation=True,create_public_threads=True, create_private_threads = True
                                                            ,send_messages_in_threads = True)
                    await category_obj.set_permissions(member_object, overwrite=cat_perms,
                                                       reason=f"Dołączył(a) do party '{party_data['party_name']}'")
                else:
                    # Fallback na indywidualne kanały, jeśli kategoria nie istnieje
                    channels_to_update_perms_fallback = []
                    if party_data.get("text_channel_id"):
                        channels_to_update_perms_fallback.append(
                            (party_data["text_channel_id"],
                             {"view_channel": True, "send_messages": True, "read_message_history": True})
                        )
                    if party_data.get("voice_channel_id"):
                        channels_to_update_perms_fallback.append(
                            (party_data["voice_channel_id"],
                             {"view_channel": True, "connect": True, "speak": True, "stream": True,
                              "use_voice_activation": True})
                        )
                    if party_data.get("voice_channel_id_2"):
                        channels_to_update_perms_fallback.append(
                            (party_data["voice_channel_id_2"],
                             {"view_channel": True, "connect": True, "speak": True, "stream": True,
                              "use_voice_activation": True})
                        )
                    for channel_id, perms_dict in channels_to_update_perms_fallback:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            perm_overwrite = disnake.PermissionOverwrite(**perms_dict)
                            await channel.set_permissions(member_object, overwrite=perm_overwrite,
                                                          reason=f"Dołączył(a) do party '{party_data['party_name']}' (fallback)")

                if self.requesting_user_id not in party_data["member_ids"]:
                    party_data["member_ids"].append(self.requesting_user_id)

                save_party_data()

                await self.party_cog._update_party_emblem(self.party_id)
                if party_data.get("settings_channel_id"):
                    await self.party_cog._update_settings_embed(self.party_id)

                await interaction.followup.send(
                    f"Zaakceptowano prośbę od {requesting_user.mention} o dołączenie do party '{party_data['party_name']}'.",
                    ephemeral=True)

                try:
                    await requesting_user.send(
                        f"Twoja prośba o dołączenie do party '{party_data['party_name']}' (ID: `{self.party_id}`) została ZAACCEPTOWANA!")
                except disnake.Forbidden:
                    await interaction.followup.send(
                        f"Nie udało się wysłać powiadomienia DM do {requesting_user.mention} (może mieć zablokowane DM). Dodano go jednak do party.",
                        ephemeral=True)

                party_text_channel_id = party_data.get("text_channel_id")
                if party_text_channel_id:
                    party_text_channel = guild.get_channel(party_text_channel_id)
                    if party_text_channel and isinstance(party_text_channel, disnake.TextChannel):
                        try:
                            await party_text_channel.send(
                                f"🎉 {member_object.mention} dołączył(a) do party na zaproszenie lidera!")
                        except disnake.HTTPException:
                            print(
                                f"WARN: Nie udało się wysłać wiadomości o dołączeniu na kanał tekstowy party {self.party_id}")

            except Exception as e:
                await interaction.followup.send(f"Wystąpił nieoczekiwany błąd podczas dodawania użytkownika: {e}",
                                                ephemeral=True)
                print(
                    f"BŁĄD KRYTYCZNY przy akceptacji dołączenia dla party {self.party_id} (user: {self.requesting_user_id}): {e}")
        else:
            await interaction.followup.send(
                f"Odrzucono prośbę od {requesting_user.mention} o dołączenie do party '{party_data.get('party_name', 'Nieznane Party')}'.",
                ephemeral=True)
            try:
                await requesting_user.send(
                    f"Twoja prośba o dołączenie do party '{party_data.get('party_name', 'Nieznane Party')}' (ID: `{self.party_id}`) została ODRZUCONA.")
            except disnake.Forbidden:
                pass
        self.stop()

    @disnake.ui.button(label="Tak, akceptuj", style=disnake.ButtonStyle.success, custom_id="join_accept")
    async def accept_join(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await interaction.response.defer(ephemeral=True)
        await self.handle_decision(interaction, accepted=True)

    @disnake.ui.button(label="Nie, odrzuć", style=disnake.ButtonStyle.danger, custom_id="join_reject")
    async def reject_join(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await interaction.response.defer(ephemeral=True)
        await self.handle_decision(interaction, accepted=False)

    async def on_timeout(self):
        if self.decision_made: return
        self.decision_made = True

        from .party_manager import active_parties, save_party_data

        party_data = active_parties.get(self.party_id)

        if party_data and self.requesting_user_id in party_data.get("pending_join_requests", []):
            party_data["pending_join_requests"].remove(self.requesting_user_id)
            save_party_data()

        requesting_user = self.bot.get_user(self.requesting_user_id)
        if not requesting_user:
            try:
                requesting_user = await self.bot.fetch_user(self.requesting_user_id)
            except (disnake.NotFound, disnake.HTTPException):
                print(
                    f"INFO: Nie można pobrać użytkownika {self.requesting_user_id} przy timeout prośby o dołączenie do party {self.party_id}.")
                self.stop()
                return

        if requesting_user and party_data:
            try:
                await requesting_user.send(
                    f"Twoja prośba o dołączenie do party '{party_data.get('party_name', 'Nieznane Party')}' (ID: `{self.party_id}`) "
                    f"wygasła z powodu braku odpowiedzi od lidera w wyznaczonym czasie.")
            except disnake.Forbidden:
                pass

        leader_user = self.bot.get_user(party_data.get("leader_id")) if party_data else None
        if leader_user:
            try:
                await leader_user.send(
                    f"Prośba o dołączenie od {requesting_user.mention if requesting_user else f'ID:{self.requesting_user_id}'} "
                    f"do Twojego party '{party_data.get('party_name', 'Nieznane Party')}' (ID: `{self.party_id}`) wygasła (nie podjąłeś decyzji na czas)."
                )
            except disnake.Forbidden:
                pass

        print(
            f"INFO: Prośba o dołączenie od {self.requesting_user_id} do party {self.party_id} wygasła (timeout widoku).")
        self.stop()
