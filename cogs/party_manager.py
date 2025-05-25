# party_bot/cogs/party_manager.py

import disnake
from disnake.ext import commands, tasks
import asyncio
import datetime
# import uuid  # Ten import wydaje się nieużywany w tym pliku # USUNIĘTO
import json
import os

# Importy z tego samego pakietu (cogs) i katalogu nadrzędnego (dla config)
import config  # Zakłada, że config.py jest w Party_bot/
from cogs import party_creation_flow
from cogs.party_join_logic import JoinRequestApprovalView
from cogs.party_leader_actions import LeaderControlPanelView

# --- Globalny Stan dla tego Modułu (dostępny dla funkcji w tym pliku i dla Coga) ---
active_parties = {}
parties_awaiting_extension_reply = {}


# --- Funkcje Persystencji ---
def _ensure_data_dir_exists():
    if not os.path.exists(config.DATA_DIR):
        try:
            os.makedirs(config.DATA_DIR)
            print(f"INFO: Utworzono katalog danych: {config.DATA_DIR}")
        except OSError as e:
            print(f"BŁĄD KRYTYCZNY: Nie można utworzyć katalogu danych {config.DATA_DIR}: {e}")


def save_party_data():
    _ensure_data_dir_exists()
    try:
        data_to_save = {}
        for party_id, party_data_instance in active_parties.items():
            data_to_save[party_id] = {
                "emblem_message_id": party_data_instance.get("emblem_message_id"),
                "guild_id": party_data_instance.get("guild_id"),
                "leader_id": party_data_instance.get("leader_id"),
                "party_name": party_data_instance.get("party_name"),
                "game_name": party_data_instance.get("game_name"),
                "category_id": party_data_instance.get("category_id"),
                "settings_channel_id": party_data_instance.get("settings_channel_id"),
                "settings_embed_message_id": party_data_instance.get("settings_embed_message_id"),
                "text_channel_id": party_data_instance.get("text_channel_id"),
                "voice_channel_id": party_data_instance.get("voice_channel_id"),
                "voice_channel_id_2": party_data_instance.get("voice_channel_id_2"),
                "member_ids": party_data_instance.get("member_ids", []),
                "pending_join_requests": party_data_instance.get("pending_join_requests", []),
                "expiry_timestamp": party_data_instance.get("expiry_timestamp"),
                "next_reminder_timestamp": party_data_instance.get("next_reminder_timestamp"),
                "reminder_sent_for_current_cycle": party_data_instance.get("reminder_sent_for_current_cycle", False),
                "leader_panel_dm_id": party_data_instance.get("leader_panel_dm_id"),
                "extension_reminder_dm_id": party_data_instance.get("extension_reminder_dm_id")
            }
        with open(config.PARTY_DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
    except IOError as e:
        print(f"BŁĄD: Nie udało się zapisać danych party do {config.PARTY_DATA_FILE}: {e}")
    except TypeError as e:
        print(f"BŁĄD: Problem z serializacją danych party (TypeError): {e}")
    except Exception as e:
        print(f"BŁĄD KRYTYCZNY: Nieoczekiwany błąd podczas zapisywania danych party: {e}")


def load_party_data():
    global active_parties
    _ensure_data_dir_exists()
    if os.path.exists(config.PARTY_DATA_FILE):
        try:
            with open(config.PARTY_DATA_FILE, 'r') as f:
                loaded_data = json.load(f)
                active_parties = {int(k): v for k, v in loaded_data.items()}
                print(f"INFO: Dane party załadowane z {config.PARTY_DATA_FILE}. Liczba party: {len(active_parties)}")
                for party_id, party_data_instance in list(active_parties.items()):
                    party_data_instance["reminder_sent_for_current_cycle"] = False
                    if party_id in parties_awaiting_extension_reply:
                        del parties_awaiting_extension_reply[party_id]
        except (IOError, json.JSONDecodeError) as e:
            print(
                f"BŁĄD: Nie udało się załadować danych party z {config.PARTY_DATA_FILE}: {e}. Rozpoczynam z pustym stanem.")
            active_parties = {}
        except Exception as e:
            print(
                f"BŁĄD KRYTYCZNY: Nieoczekiwany błąd podczas ładowania danych party: {e}. Rozpoczynam z pustym stanem.")
            active_parties = {}
    else:
        print(f"INFO: Plik danych {config.PARTY_DATA_FILE} nie istnieje. Rozpoczynam z pustym stanem.")
        active_parties = {}


class PartySettingsView(disnake.ui.View):
    def __init__(self, party_id: int):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(label="Poproś o Dołączenie", style=disnake.ButtonStyle.success,
                                        custom_id=f"settings_request_join_{party_id}"))
        self.add_item(disnake.ui.Button(label="Opuść Party", style=disnake.ButtonStyle.danger,
                                        custom_id=f"settings_leave_party_{party_id}"))


class PartyManagementCog(commands.Cog, name="Zarządzanie Party"):
    def __init__(self, bot_instance: commands.Bot):
        self.bot = bot_instance
        load_party_data()
        self.extension_check_loop.start()
        print("Cog 'Zarządzanie Party' został załadowany.")

    def cog_unload(self):
        self.extension_check_loop.cancel()
        save_party_data()
        print("Cog 'Zarządzanie Party' został odładowany, dane zapisane.")

    async def _start_party_creation_from_interaction(self, interaction: disnake.MessageInteraction):
        author = interaction.user
        guild = interaction.guild

        is_already_leader = any(p_data.get("leader_id") == author.id for p_data in active_parties.values())
        if is_already_leader:
            leader_of_party_name = next((p_data.get("party_name", "...") for p_data in active_parties.values() if
                                         p_data.get("leader_id") == author.id), "nieznanego party")
            msg = f"{author.mention}, jesteś już liderem party '{leader_of_party_name}'. Możesz prowadzić tylko jedno party."
            await interaction.followup.send(msg, ephemeral=True)
            return

        try:
            dm_ch = await author.create_dm()
        except disnake.Forbidden:
            await interaction.followup.send(f"{author.mention}, nie mogę Ci wysłać DM. Sprawdź ustawienia prywatności.",
                                            ephemeral=True)
            return

        await interaction.followup.send(
            "Rozpoczynam proces tworzenia party w Twoich wiadomościach prywatnych (DM)... Sprawdź DM!", ephemeral=True)

        selected_game = await party_creation_flow.handle_game_selection_dm(self.bot, author, dm_ch)
        if not selected_game: return

        party_name_input = await party_creation_flow.handle_party_name_dm(self.bot, author, dm_ch)
        if not party_name_input: return

        leader = author

        szukam_ch = disnake.utils.get(guild.text_channels, name=config.SZUKAM_PARTY_CHANNEL_NAME)
        if not szukam_ch:
            await dm_ch.send(
                f"Krytyczny błąd: Kanał `#{config.SZUKAM_PARTY_CHANNEL_NAME}` nie został znaleziony na serwerze '{guild.name}'.")
            return

        cat_name = f"🎉 {party_name_input} ({leader.display_name})"
        category_overwrites = {
            guild.default_role: disnake.PermissionOverwrite(
                view_channel=True, read_messages=True, send_messages=False, connect=False, speak=False,
                create_public_threads=False, create_private_threads=False, send_messages_in_threads=False
            ),
            guild.me: disnake.PermissionOverwrite(
                view_channel=True, manage_channels=True, manage_permissions=True, read_messages=True,
                send_messages=True,
                connect=True, speak=True, create_public_threads=True, create_private_threads=True,
                send_messages_in_threads=True, manage_threads=True
            ),
            leader: disnake.PermissionOverwrite(
                view_channel=True, read_messages=True, send_messages=True, connect=True, speak=True,
                manage_messages=True,
                mute_members=True, deafen_members=True, move_members=True, create_public_threads=True,
                create_private_threads=True, send_messages_in_threads=True
            )
        }
        category = None;
        settings_ch = None;
        text_ch = None;
        voice_ch1 = None;
        voice_ch2 = None
        try:
            category = await guild.create_category(name=cat_name, overwrites=category_overwrites)
            settings_ch_name = f"📌︱info-{party_name_input[:20]}"
            settings_ch_overwrites = {
                guild.default_role: disnake.PermissionOverwrite(send_messages=False, add_reactions=False,
                                                                create_public_threads=False,
                                                                create_private_threads=False,
                                                                send_messages_in_threads=False),
                guild.me: disnake.PermissionOverwrite(send_messages=True, embed_links=True, manage_messages=True)
            }
            settings_ch = await category.create_text_channel(name=settings_ch_name, overwrites=settings_ch_overwrites)
            text_ch_name = f"💬︱{party_name_input[:20]}"
            text_ch = await category.create_text_channel(name=text_ch_name)
            await text_ch.send(
                f"Witaj w party **{party_name_input}**! Lider: {leader.mention}. Gra: **{selected_game}**.")
            voice_ch1_name = f"🔊︱Głos 1 ({party_name_input[:15]})"
            voice_ch1 = await category.create_voice_channel(name=voice_ch1_name)
            voice_ch2_name = f"🔊︱Głos 2 ({party_name_input[:15]})"
            voice_ch2 = await category.create_voice_channel(name=voice_ch2_name)
        except disnake.HTTPException as e:
            await dm_ch.send(f"Nie udało się stworzyć kanałów: {e}.")
            if category:
                for c_del in list(category.channels):
                    try:
                        await c_del.delete()
                    except disnake.HTTPException:
                        pass
                try:
                    await category.delete()
                except disnake.HTTPException:
                    pass
            return

        emb = disnake.Embed(title=f"✨ Nowe Party: {party_name_input}", description="Poproś o dołączenie!",
                            color=disnake.Color.green())
        emb.add_field(name="🎮 Gra", value=selected_game, inline=True)
        emb.add_field(name="👑 Lider", value=leader.mention, inline=True)
        emb.add_field(name="👥 Członkowie", value=leader.mention, inline=False)
        emb.set_footer(text="ID Party zostanie przypisane po wysłaniu.")
        pub_join_view = disnake.ui.View(timeout=None)
        pub_join_btn = disnake.ui.Button(label="Poproś o Dołączenie", style=disnake.ButtonStyle.primary,
                                         custom_id=f"request_join_party_TEMP_ID")
        pub_join_view.add_item(pub_join_btn)
        emblem_message = None
        try:
            emblem_message = await szukam_ch.send(embed=emb, view=pub_join_view)
        except disnake.HTTPException as e:
            await dm_ch.send(f"Nie udało się opublikować ogłoszenia: {e}")
            if category:
                for c_del in list(category.channels):
                    try:
                        await c_del.delete()
                    except disnake.HTTPException:
                        pass
                try:
                    await category.delete()
                except disnake.HTTPException:
                    pass
            return

        party_id = emblem_message.id
        pub_join_btn.custom_id = f"request_join_party_{party_id}"
        emb.set_footer(text=f"ID Party: {party_id}")
        try:
            await emblem_message.edit(embed=emb, view=pub_join_view)
        except disnake.HTTPException as e:
            print(f"WARN: Aktualizacja custom_id przycisku dla '{party_name_input}': {e}")

        init_exp_ts = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=config.PARTY_LIFESPAN_HOURS)).timestamp()
        next_rem_ts = init_exp_ts - datetime.timedelta(
            hours=config.EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY).total_seconds()
        if config.PARTY_LIFESPAN_HOURS <= config.EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY:
            next_rem_ts = init_exp_ts

        active_parties[party_id] = {
            "emblem_message_id": party_id, "guild_id": guild.id, "leader_id": leader.id,
            "party_name": party_name_input, "game_name": selected_game,
            "category_id": category.id if category else None,
            "settings_channel_id": settings_ch.id if settings_ch else None, "settings_embed_message_id": None,
            "text_channel_id": text_ch.id if text_ch else None,
            "voice_channel_id": voice_ch1.id if voice_ch1 else None,
            "voice_channel_id_2": voice_ch2.id if voice_ch2 else None,
            "member_ids": [leader.id], "pending_join_requests": [],
            "expiry_timestamp": init_exp_ts, "next_reminder_timestamp": next_rem_ts,
            "reminder_sent_for_current_cycle": False, "leader_panel_dm_id": None, "extension_reminder_dm_id": None
        }
        if settings_ch: await self._update_settings_embed(party_id)
        save_party_data()
        try:
            await dm_ch.send(f"Party '{party_name_input}' stworzone! Panel zarządzania został wysłany.",
                             delete_after=config.DM_MESSAGE_DELETE_DELAY)
        except disnake.HTTPException:
            pass
        await self.send_leader_control_panel(leader, party_id)

    # --- JEDYNA ZMIANA TUTAJ ---
    @commands.slash_command(
        name="setup_party_creation",
        description="Wysyła wiadomość z przyciskiem do tworzenia party na kanale 'stworz-party'."
    )
    @commands.has_permissions(administrator=True)
    async def setup_party_creation_command(self, inter: disnake.ApplicationCommandInteraction):
        # NATYCHMIASTOWE ODROCZENIE ODPOWIEDZI - KLUCZOWE DLA SLASH COMMANDS
        print(f"[{datetime.datetime.now()}] DEBUG: Wywołano setup_party_creation_command przez {inter.user}")
        try:
            await inter.response.defer(ephemeral=True)
            print(
                f"[{datetime.datetime.now()}] DEBUG: Interakcja setup_party_creation_command ODROCZONA dla {inter.user}")
        except Exception as e_defer:
            print(
                f"[{datetime.datetime.now()}] KRYTYCZNY BŁĄD: Nie udało się odroczyć interakcji w setup_party_creation_command: {e_defer}")
            return

        target_channel_name = config.STWORZ_PARTY_CHANNEL_NAME

        if not target_channel_name or not isinstance(target_channel_name, str):
            print(
                f"BŁĄD KRYTYCZNY: config.STWORZ_PARTY_CHANNEL_NAME nie jest poprawnie zdefiniowana w pliku config.py! Aktualna wartość: {target_channel_name}")
            await inter.followup.send(  # ZMIANA: inter.followup.send
                "Błąd krytyczny konfiguracji bota: Nazwa kanału 'stworz-party' nie jest ustawiona. Skontaktuj się z administratorem bota.",
                ephemeral=True
            )
            return

        stworz_party_channel = disnake.utils.get(inter.guild.text_channels, name=target_channel_name)

        if not stworz_party_channel:
            await inter.followup.send(  # ZMIANA: inter.followup.send
                f"Nie znaleziono kanału `#{target_channel_name}`. Utwórz go najpierw.",
                ephemeral=True
            )
            return

        embed = disnake.Embed(
            title="🎉 Stwórz Nowe Party!",
            description=(
                "Kliknij poniższy przycisk, aby rozpocząć proces tworzenia party.\n"
                "Zostaniesz poprowadzony przez kolejne kroki w wiadomościach prywatnych (DM)."
            ),
            color=disnake.Color.green()
        )
        view = disnake.ui.View(timeout=None)
        view.add_item(disnake.ui.Button(
            label="Stwórz Party",
            style=disnake.ButtonStyle.success,
            custom_id="create_new_party_button_from_setup",  # To ID jest obsługiwane w on_button_interaction
            emoji="🎉"
        ))

        try:
            await stworz_party_channel.send(embed=embed, view=view)
            await inter.followup.send(  # ZMIANA: inter.followup.send
                f"Wiadomość z przyciskiem do tworzenia party została wysłana na {stworz_party_channel.mention}.",
                ephemeral=True
            )
        except disnake.Forbidden:
            print(
                f"BŁĄD: Brak uprawnień do wysłania wiadomości na kanale {stworz_party_channel.mention} (serwer: {inter.guild.name if inter.guild else 'Nieznany'})")
            await inter.followup.send(  # ZMIANA: inter.followup.send
                f"Nie mam uprawnień do wysłania wiadomości na kanale {stworz_party_channel.mention}.",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error sending party creation setup message: {e} (Typ: {type(e)})")
            await inter.followup.send(f"Wystąpił nieoczekiwany błąd podczas wysyłania wiadomości: {type(e).__name__}",
                                      ephemeral=True)  # ZMIANA: inter.followup.send

    @setup_party_creation_command.error
    async def setup_party_creation_command_error(self, inter: disnake.ApplicationCommandInteraction,
                                                 error: commands.CommandError):
        print(f"Error handler dla setup_party_creation_command przechwycił błąd: {error} (Typ: {type(error)})")

        if not inter.response.is_done():
            # Jeśli defer() się nie powiodło lub nie zostało wywołane
            try:
                if isinstance(error, commands.MissingPermissions):
                    await inter.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
                else:
                    await inter.response.send_message(
                        f"Wystąpił błąd przed przetworzeniem komendy: {type(error).__name__}.", ephemeral=True)
            except Exception as e_resp:
                print(f"Nie udało się wysłać response w error handlerze (is_done() było FALSE): {e_resp}")
        else:
            # Jeśli interakcja była już odroczona, użyj followup
            try:
                if isinstance(error,
                              commands.MissingPermissions):  # To powinno być złapane przez dekorator @has_permissions
                    await inter.followup.send("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
                else:
                    await inter.followup.send(
                        f"Wystąpił błąd po rozpoczęciu przetwarzania komendy: {type(error).__name__}.", ephemeral=True)
            except Exception as e_followup:
                print(f"Nie udało się wysłać followup w error handlerze: {e_followup}")

    # --- KONIEC JEDYNEJ ZMIANY ---

    async def _update_settings_embed(self, party_id: int):
        party_data = active_parties.get(party_id)
        if not party_data or not party_data.get("settings_channel_id"):
            return
        guild = self.bot.get_guild(party_data["guild_id"])
        if not guild: return
        settings_channel = guild.get_channel(party_data["settings_channel_id"])
        if not settings_channel or not isinstance(settings_channel, disnake.TextChannel): return
        leader = guild.get_member(party_data["leader_id"])
        members_mentions = [guild.get_member(mid).mention if guild.get_member(mid) else f"ID:{mid}" for mid in
                            party_data.get("member_ids", [])]
        embed_title = f"⚙️ Informacje o Party: {party_data['party_name']}"
        embed_color = disnake.Color.dark_grey()
        embed = disnake.Embed(title=embed_title, color=embed_color)
        embed.add_field(name="👑 Lider", value=leader.mention if leader else f"ID: {party_data['leader_id']}",
                        inline=False)
        embed.add_field(name="👥 Aktualni Członkowie",
                        value="\n".join(members_mentions) if members_mentions else "Brak członków.", inline=False)
        embed.add_field(name="🆔 ID Party (Emblematu Głównego)", value=f"`{party_id}`",
                        inline=False)
        view = PartySettingsView(party_id)
        if party_data.get("settings_embed_message_id"):
            try:
                settings_embed_msg = await settings_channel.fetch_message(party_data["settings_embed_message_id"])
                await settings_embed_msg.edit(embed=embed, view=view)
                return
            except disnake.NotFound:
                print(f"INFO: Poprzednia wiadomość embedu ustawień dla party {party_id} nie znaleziona. Tworzę nową.")
                party_data["settings_embed_message_id"] = None
            except disnake.HTTPException as e:
                print(f"BŁĄD: Aktualizacja embedu ustawień dla party {party_id} nie powiodła się (HTTPException): {e}")
                party_data["settings_embed_message_id"] = None
            except Exception as e:
                print(f"BŁĄD: Nieoczekiwany błąd podczas aktualizacji embedu ustawień dla party {party_id}: {e}")
                party_data["settings_embed_message_id"] = None
        try:
            new_settings_embed_msg = await settings_channel.send(embed=embed, view=view)
            party_data["settings_embed_message_id"] = new_settings_embed_msg.id
            save_party_data()
        except disnake.Forbidden:
            print(f"BŁĄD: Bot nie ma uprawnień do wysyłania wiadomości na kanale ustawień party {party_id}.")
        except Exception as e:
            print(f"BŁĄD: Wysyłanie nowego embedu ustawień dla party {party_id}: {e}")

    async def _update_party_emblem(self, party_id: int):
        party_data = active_parties.get(party_id)
        if not party_data: return
        guild = self.bot.get_guild(party_data["guild_id"])
        if not guild: return
        szukam_party_channel = disnake.utils.get(guild.text_channels, name=config.SZUKAM_PARTY_CHANNEL_NAME)
        if not szukam_party_channel: return
        try:
            emblem_message = await szukam_party_channel.fetch_message(party_data["emblem_message_id"])
            leader = guild.get_member(party_data["leader_id"])
            members_mentions = [guild.get_member(mid).mention if guild.get_member(mid) else f"ID:{mid}" for mid in
                                party_data.get("member_ids", [])]
            embed = disnake.Embed(title=f"✨ Party: {party_data['party_name']}",
                                  description="Poproś o dołączenie!",
                                  color=disnake.Color.blurple())
            embed.add_field(name="🎮 Gra", value=party_data["game_name"], inline=True)
            embed.add_field(name="👑 Lider", value=leader.mention if leader else f"ID:{party_data['leader_id']}",
                            inline=True)
            embed.add_field(name="👥 Członkowie", value="\n".join(members_mentions) if members_mentions else "Brak",
                            inline=False)
            embed.set_footer(text=f"ID Party: {party_id}")
            view = disnake.ui.View(timeout=None)
            view.add_item(disnake.ui.Button(label="Poproś o Dołączenie", style=disnake.ButtonStyle.primary,
                                            custom_id=f"request_join_party_{party_id}"))
            await emblem_message.edit(embed=embed, view=view)
        except disnake.NotFound:
            print(
                f"INFO: Nie znaleziono emblematu {party_data.get('emblem_message_id')} dla '{party_data.get('party_name')}'. Mógł zostać usunięty.")
        except Exception as e:
            print(f"BŁĄD: Aktualizacja emblematu '{party_data.get('party_name')}': {e}")

    async def send_leader_control_panel(self, leader: disnake.User, party_id: int):
        party_data = active_parties.get(party_id)
        if not party_data: return
        try:
            dm_channel = await leader.create_dm()
            guild = self.bot.get_guild(party_data["guild_id"])
            members_list_str = [
                f"- {guild.get_member(m_id).mention if guild and guild.get_member(m_id) else f'ID:{m_id}'} (`{m_id}`)"
                for m_id in party_data.get("member_ids", [])
            ]
            embed = disnake.Embed(
                title=f"🛠️ Panel Party: {party_data['party_name']}",
                description=f"**Gra:** {party_data['game_name']}\n**Wygasa:** <t:{int(party_data['expiry_timestamp'])}:F> (<t:{int(party_data['expiry_timestamp'])}:R>)",
                color=disnake.Color.gold()
            )
            embed.add_field(name="👥 Aktualni Członkowie:",
                            value="\n".join(members_list_str) if members_list_str else "Brak", inline=False)
            embed.add_field(
                name="Akcje (komendy w tej konwersacji DM):",
                value=(f"- `{config.DEFAULT_COMMAND_PREFIX}usun_czlonka ID_lub_@wzmianka`\n"
                       f"- `{config.DEFAULT_COMMAND_PREFIX}zmien_nazwe_party nowa nazwa`\n"
                       f"- `{config.DEFAULT_COMMAND_PREFIX}lista_czlonkow` (odświeża ten panel)\n"
                       f"- `{config.DEFAULT_COMMAND_PREFIX}opusc ID_party_lub_nazwa_party`\n"
                       f"*(Przycisk 'Rozwiąż Party' jest poniżej)*"),
                inline=False
            )
            embed.set_footer(text=f"ID Twojego Party (dla bota): {party_id}")
            view = LeaderControlPanelView(party_id)
            if party_data.get("leader_panel_dm_id"):
                try:
                    old_panel_msg = await dm_channel.fetch_message(party_data["leader_panel_dm_id"])
                    await old_panel_msg.delete()
                except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                    pass
                party_data["leader_panel_dm_id"] = None
            new_panel_msg = await dm_channel.send(embed=embed, view=view)
            party_data["leader_panel_dm_id"] = new_panel_msg.id
            save_party_data()
        except disnake.Forbidden:
            print(f"DM ERR: Nie można wysłać panelu lidera do {leader.name} ({leader.id}).")
        except Exception as e:
            print(f"ERR: Nieoczekiwany błąd przy wysyłaniu panelu lidera: {e} (Typ: {type(e)})")

    async def disband_party(self, party_id: int, reason: str = "Party rozwiązane."):
        party_data = active_parties.pop(party_id, None)
        if not party_data: return
        if party_id in parties_awaiting_extension_reply: del parties_awaiting_extension_reply[party_id]
        guild = self.bot.get_guild(party_data["guild_id"])
        if guild:
            leader_for_panel_dm = self.bot.get_user(party_data["leader_id"])
            if leader_for_panel_dm and party_data.get("leader_panel_dm_id"):
                try:
                    dm_ch = await leader_for_panel_dm.create_dm()
                    msg_to_delete = await dm_ch.fetch_message(party_data["leader_panel_dm_id"])
                    await msg_to_delete.delete()
                except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                    pass
            if party_data.get("category_id"):
                category = guild.get_channel(party_data["category_id"])
                if category and isinstance(category, disnake.CategoryChannel):
                    for ch_in_cat in list(category.channels):
                        try:
                            await ch_in_cat.delete(reason=reason)
                        except disnake.HTTPException:
                            pass
                    try:
                        await category.delete(reason=reason)
                    except disnake.HTTPException:
                        pass
            else:
                channel_keys_to_delete_individually = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                                       "voice_channel_id_2"]
                for ch_key in channel_keys_to_delete_individually:
                    ch_id_to_del = party_data.get(ch_key)
                    if ch_id_to_del:
                        channel_to_delete = guild.get_channel(ch_id_to_del)
                        if channel_to_delete:
                            try:
                                await channel_to_delete.delete(
                                    reason=f"{reason} (kanał poza kategorią lub kategoria nie znaleziona)")
                            except disnake.HTTPException:
                                pass
            if party_data.get("emblem_message_id"):
                szukam_ch = disnake.utils.get(guild.text_channels, name=config.SZUKAM_PARTY_CHANNEL_NAME)
                if szukam_ch:
                    try:
                        msg = await szukam_ch.fetch_message(party_data["emblem_message_id"])
                        await msg.delete()
                    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                        pass
        else:
            print(
                f"WARN: Gildia {party_data['guild_id']} niedostępna przy rozwiązywaniu party {party_id}. Usuwam tylko dane.")
        save_party_data()
        leader = self.bot.get_user(party_data["leader_id"])
        if leader:
            try:
                await leader.send(
                    f"Twoje party '{party_data.get('party_name', 'N/A')}' zostało rozwiązane. Powód: {reason}")
            except disnake.Forbidden:
                pass
        print(f"INFO: Party '{party_data.get('party_name', 'N/A')}' (ID: {party_id}) rozwiązane.")

    # ZAKOMENTOWANA KOMENDA !party - ZASTĄPIONA PRZEZ PRZYCISK
    # @commands.command(name="party")
    # async def party_command_handler(self, ctx: commands.Context):
    #   ... (reszta zakomentowanej komendy)

    @commands.Cog.listener("on_interaction")
    async def on_button_interaction(self, interaction: disnake.MessageInteraction):
        custom_id = interaction.data.get("custom_id")
        if not custom_id: return

        if custom_id == "create_new_party_button_from_setup":  # To jest ID przycisku z komendy setup
            await interaction.response.defer(ephemeral=True)
            await self._start_party_creation_from_interaction(interaction)
            return

        elif custom_id.startswith("request_join_party_") or custom_id.startswith("settings_request_join_"):
            await interaction.response.defer(ephemeral=True)
            try:
                if custom_id.startswith("settings_request_join_"):
                    party_id_str = custom_id.replace("settings_request_join_", "")
                else:
                    party_id_str = custom_id.replace("request_join_party_", "")
                party_id = int(party_id_str)
            except (IndexError, ValueError):
                await interaction.followup.send("Błąd wewnętrzny przycisku (ID party).", ephemeral=True);
                return
            user_requesting_join = interaction.user
            party_data = active_parties.get(party_id)
            if not party_data:
                await interaction.followup.send("To party już nie istnieje lub wystąpił błąd.", ephemeral=True);
                return
            if user_requesting_join.id == party_data["leader_id"]:
                await interaction.followup.send("Jesteś liderem tego party, nie musisz prosić o dołączenie.",
                                                ephemeral=True);
                return
            if user_requesting_join.id in party_data["member_ids"]:
                await interaction.followup.send("Już jesteś członkiem tego party!", ephemeral=True);
                return
            if user_requesting_join.id in party_data.get("pending_join_requests", []):
                await interaction.followup.send(
                    "Twoja prośba o dołączenie do tego party już oczekuje na akceptację lidera.", ephemeral=True);
                return
            leader = self.bot.get_user(party_data["leader_id"])
            if not leader:
                try:
                    leader = await self.bot.fetch_user(party_data["leader_id"])
                except disnake.NotFound:
                    await interaction.followup.send("Lider tego party jest obecnie nieosiągalny.", ephemeral=True);
                    return
                except disnake.HTTPException:
                    await interaction.followup.send("Wystąpił błąd sieciowy przy próbie kontaktu z liderem.",
                                                    ephemeral=True);
                    return
            try:
                if user_requesting_join.id not in party_data.get("pending_join_requests", []):
                    party_data.setdefault("pending_join_requests", []).append(user_requesting_join.id)
                    save_party_data()
                leader_dm_channel = await leader.create_dm()
                approval_view = JoinRequestApprovalView(party_id, user_requesting_join.id, self.bot, self)
                await leader_dm_channel.send(
                    f"Użytkownik {user_requesting_join.mention} (`{user_requesting_join.id}`) chce dołączyć do Twojego party: **{party_data['party_name']}**.",
                    view=approval_view
                )
                await interaction.followup.send("Twoja prośba o dołączenie została wysłana do lidera party.",
                                                ephemeral=True)
            except disnake.Forbidden:
                if user_requesting_join.id in party_data.get("pending_join_requests", []):
                    party_data["pending_join_requests"].remove(user_requesting_join.id)
                    save_party_data()
                await interaction.followup.send(
                    "Nie udało się wysłać prośby do lidera (prawdopodobnie ma zablokowane DM).", ephemeral=True)
            except Exception as e:
                if user_requesting_join.id in party_data.get("pending_join_requests", []):
                    party_data["pending_join_requests"].remove(user_requesting_join.id)
                    save_party_data()
                await interaction.followup.send(f"Wystąpił błąd przy wysyłaniu prośby: {e}", ephemeral=True)
                print(f"BŁĄD przycisku dołączania (party {party_id}, user {user_requesting_join.id}): {e}")

        elif custom_id.startswith("settings_leave_party_"):
            await interaction.response.defer(ephemeral=True)
            try:
                party_id = int(custom_id.split("_")[3])
            except (IndexError, ValueError):
                await interaction.followup.send("Błąd wewnętrzny przycisku.", ephemeral=True);
                return
            leaver = interaction.user
            party_data = active_parties.get(party_id)
            if not party_data:
                await interaction.followup.send("To party już nie istnieje.", ephemeral=True);
                return
            if leaver.id == party_data["leader_id"]:
                await interaction.followup.send("Lider nie może opuścić party w ten sposób.", ephemeral=True);
                return
            if leaver.id not in party_data["member_ids"]:
                await interaction.followup.send("Nie jesteś członkiem tego party.", ephemeral=True);
                return
            guild = self.bot.get_guild(party_data["guild_id"])
            if not guild:
                await interaction.followup.send("Błąd serwera.", ephemeral=True);
                return
            member_obj = guild.get_member(leaver.id)
            channels_to_clear_perms_keys = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                            "voice_channel_id_2"]
            category_id = party_data.get("category_id")
            category_obj = guild.get_channel(category_id) if category_id else None
            if member_obj:
                if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                    try:
                        await category_obj.set_permissions(member_obj, overwrite=None,
                                                           reason="Opuścił party (przycisk z kanału ustawień)")
                    except disnake.HTTPException as e:
                        print(
                            f"BŁĄD przy usuwaniu uprawnień dla {leaver.id} z kategorii {category_id} (party {party_id}): {e}")
                else:
                    for ch_key in channels_to_clear_perms_keys:
                        ch_id = party_data.get(ch_key)
                        if not ch_id: continue
                        channel = guild.get_channel(ch_id)
                        if channel:
                            try:
                                await channel.set_permissions(member_obj, overwrite=None,
                                                              reason="Opuścił party (przycisk z kanału ustawień)")
                            except disnake.HTTPException as e:
                                print(
                                    f"BŁĄD przy usuwaniu uprawnień dla {leaver.id} z kanału {ch_id} (party {party_id}): {e}")
            if leaver.id in party_data["member_ids"]: party_data["member_ids"].remove(leaver.id)
            save_party_data()
            await self._update_party_emblem(party_id)
            await self._update_settings_embed(party_id)
            await interaction.followup.send(f"Pomyślnie opuściłeś/aś party '{party_data['party_name']}'.",
                                            ephemeral=True)
            leader_obj = self.bot.get_user(party_data["leader_id"])
            if not leader_obj:
                try:
                    leader_obj = await self.bot.fetch_user(party_data["leader_id"])
                except:
                    pass
            if leader_obj:
                try:
                    await leader_obj.send(
                        f"Użytkownik {leaver.mention} (`{leaver.id}`) opuścił Twoje party '{party_data['party_name']}'.")
                except disnake.Forbidden:
                    pass
                if party_id in active_parties: await self.send_leader_control_panel(leader_obj, party_id)

        elif custom_id.startswith("leader_disband_"):
            await interaction.response.defer(ephemeral=True)
            try:
                party_id = int(custom_id.split("_")[2])
            except (IndexError, ValueError):
                await interaction.followup.send("Błąd wewnętrzny przycisku 'Rozwiąż'.", ephemeral=True);
                return
            party_data_check = active_parties.get(party_id)
            if not party_data_check:
                await interaction.followup.send("To party już nie istnieje.", ephemeral=True);
                return
            if interaction.user.id != party_data_check["leader_id"]:
                await interaction.followup.send("Tylko lider może rozwiązać to party.", ephemeral=True);
                return
            await interaction.followup.send(f"Rozwiązywanie party '{party_data_check['party_name']}'...",
                                            ephemeral=True)
            await self.disband_party(party_id,
                                     reason=f"Rozwiązane przez lidera ({interaction.user.name}) za pomocą przycisku.")

    async def _cleanup_dm_messages(self, ctx_or_interaction, bot_message: disnake.Message = None,
                                   user_message: disnake.Message = None, delay: int = None):
        effective_delay = delay if delay is not None else config.DM_MESSAGE_DELETE_DELAY
        if user_message:
            try:
                await user_message.delete()
            except disnake.HTTPException:
                pass
        elif isinstance(ctx_or_interaction, commands.Context):
            try:
                await ctx_or_interaction.message.delete()
            except disnake.HTTPException:
                pass
        if bot_message:
            if effective_delay > 0: await asyncio.sleep(effective_delay)
            try:
                await bot_message.delete()
            except disnake.HTTPException:
                pass

    # --- Pozostałe komendy DM (opusc, usun_czlonka, etc.) i pętla tasków BEZ ZMIAN ---
    # ... (cała reszta kodu od @commands.command(name="opusc") aż do końca pliku)
    # ... (on_extension_reply, setup - wszystko to pozostaje BEZ ZMIAN)

    @commands.command(name="opusc")
    @commands.dm_only()
    async def leave_party_dm_command(self, ctx: commands.Context, *, party_identifier: str):
        leaver = ctx.author
        bot_response_msg = None
        parties_member_of_and_not_leader = [{'id': pid, 'name': pdata.get("party_name", "N/A"), 'data': pdata} for
                                            pid, pdata in
                                            active_parties.items() if
                                            leaver.id in pdata.get("member_ids", []) and leaver.id != pdata.get(
                                                "leader_id")]

        if not parties_member_of_and_not_leader:
            bot_response_msg = await ctx.send("Nie jesteś członkiem żadnego party, które mógłbyś opuścić tą komendą.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        target_party_id_to_leave = None
        target_party_data_to_leave = None
        if party_identifier.isdigit():
            party_id_candidate = int(party_identifier)
            for p_info in parties_member_of_and_not_leader:
                if p_info['id'] == party_id_candidate:
                    target_party_id_to_leave = party_id_candidate
                    target_party_data_to_leave = p_info['data'];
                    break

        if not target_party_id_to_leave:
            found_by_name = [p_info for p_info in parties_member_of_and_not_leader if
                             p_info['name'].lower() == party_identifier.lower().strip()]
            if len(found_by_name) == 1:
                target_party_id_to_leave = found_by_name[0]['id']
                target_party_data_to_leave = found_by_name[0]['data']
            elif len(found_by_name) > 1:
                options = "\n".join([f"- `{p['id']}` : {p['name']}" for p in found_by_name])
                bot_response_msg = await ctx.send(f"Jesteś członkiem kilku party o tej nazwie. Podaj ID:\n{options}")
                await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg,
                                                delay=config.DM_MESSAGE_DELETE_DELAY * 2);
                return

        if not target_party_id_to_leave or not target_party_data_to_leave:
            bot_response_msg = await ctx.send(f"Nie znaleziono party '{party_identifier}'.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        guild = self.bot.get_guild(target_party_data_to_leave["guild_id"])
        if not guild:
            bot_response_msg = await ctx.send("Błąd: Serwer party nieosiągalny.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        member_obj = guild.get_member(leaver.id)

        if member_obj:
            category_id = target_party_data_to_leave.get("category_id")
            category_obj = guild.get_channel(category_id) if category_id else None
            if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                try:
                    await category_obj.set_permissions(member_obj, overwrite=None, reason="Opuścił party (komenda DM)")
                except disnake.HTTPException:
                    pass
            else:
                channels_to_clear_keys = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                          "voice_channel_id_2"]
                for ch_key in channels_to_clear_keys:
                    ch_id = target_party_data_to_leave.get(ch_key)
                    if ch_id:
                        channel = guild.get_channel(ch_id)
                        if channel:
                            try:
                                await channel.set_permissions(member_obj, overwrite=None,
                                                              reason="Opuścił party (komenda DM)")
                            except disnake.HTTPException:
                                pass

        if leaver.id in target_party_data_to_leave["member_ids"]:
            target_party_data_to_leave["member_ids"].remove(leaver.id)
        save_party_data()
        await self._update_party_emblem(target_party_id_to_leave)
        if target_party_data_to_leave.get("settings_channel_id"):
            await self._update_settings_embed(target_party_id_to_leave)

        bot_response_msg = await ctx.send(f"Pomyślnie opuściłeś/aś party '{target_party_data_to_leave['party_name']}'.")

        leader_of_left_party = self.bot.get_user(target_party_data_to_leave["leader_id"])
        if not leader_of_left_party:
            try:
                leader_of_left_party = await self.bot.fetch_user(target_party_data_to_leave["leader_id"])
            except:
                pass

        if leader_of_left_party:
            try:
                await leader_of_left_party.send(
                    f"Użytkownik {leaver.mention} (`{leaver.id}`) opuścił Twoje party '{target_party_data_to_leave['party_name']}'.")
            except disnake.Forbidden:
                pass
            if target_party_id_to_leave in active_parties:
                await self.send_leader_control_panel(leader_of_left_party, target_party_id_to_leave)

        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @leave_party_dm_command.error
    async def leave_party_dm_command_error_handler(self, ctx, error):
        bot_response_msg = None
        if isinstance(error, commands.MissingRequiredArgument):
            if error.param.name == 'party_identifier':
                bot_response_msg = await ctx.send(
                    f"Musisz podać ID lub nazwę party, np. `{config.DEFAULT_COMMAND_PREFIX}opusc MojeParty`.")
        elif isinstance(error, commands.PrivateMessageOnly):
            pass
        else:
            bot_response_msg = await ctx.send(f"Błąd w !opusc: {type(error).__name__}")
            print(f"BŁĄD w !opusc (DM): {error}")
        if bot_response_msg:
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @commands.command(name="usun_czlonka")
    @commands.dm_only()
    async def remove_member_dm_command(self, ctx: commands.Context, *, member_identifier: str):
        leader = ctx.author
        party_id_led_by_author = next(
            (pid for pid, pdata in active_parties.items() if pdata.get("leader_id") == leader.id), None)
        bot_response_msg = None

        if not party_id_led_by_author:
            bot_response_msg = await ctx.send("Nie jesteś liderem żadnego aktywnego party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        party_id = party_id_led_by_author
        party_data = active_parties.get(party_id)

        target_user_id = None
        if member_identifier.startswith('<@') and member_identifier.endswith('>'):
            try:
                target_user_id = int(member_identifier.strip('<@!>'))
            except ValueError:
                pass
        elif member_identifier.isdigit():
            try:
                target_user_id = int(member_identifier)
            except ValueError:
                pass

        if not target_user_id:
            bot_response_msg = await ctx.send("Niepoprawny format identyfikatora. Podaj @wzmiankę lub ID.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        if target_user_id == leader.id:
            bot_response_msg = await ctx.send("Nie możesz usunąć siebie. Użyj przycisku w panelu.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        if target_user_id not in party_data.get("member_ids", []):
            bot_response_msg = await ctx.send("Tego użytkownika nie ma w Twoim party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        guild = self.bot.get_guild(party_data["guild_id"])
        if not guild:
            bot_response_msg = await ctx.send("Błąd: Serwer party nieosiągalny.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        member_to_remove_obj = guild.get_member(target_user_id)
        removed_user_mention_or_id = f"ID `{target_user_id}`"
        if member_to_remove_obj:
            removed_user_mention_or_id = f"{member_to_remove_obj.mention} (`{target_user_id}`)"

        if member_to_remove_obj:
            category_id = party_data.get("category_id")
            category_obj = guild.get_channel(category_id) if category_id else None
            if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                try:
                    await category_obj.set_permissions(member_to_remove_obj, overwrite=None,
                                                       reason="Usunięty z party przez lidera")
                except disnake.HTTPException:
                    pass
            else:
                channels_to_clear_keys = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                          "voice_channel_id_2"]
                for ch_key in channels_to_clear_keys:
                    ch_id = party_data.get(ch_key)
                    if ch_id:
                        channel = guild.get_channel(ch_id)
                        if channel:
                            try:
                                await channel.set_permissions(member_to_remove_obj, overwrite=None,
                                                              reason="Usunięty z party przez lidera")
                            except disnake.HTTPException:
                                pass

        if target_user_id in party_data.get("member_ids", []):
            party_data["member_ids"].remove(target_user_id)

        save_party_data()
        await self._update_party_emblem(party_id)
        if party_data.get("settings_channel_id"):
            await self._update_settings_embed(party_id)

        await self.send_leader_control_panel(leader, party_id)
        bot_response_msg = await ctx.send(
            f"{removed_user_mention_or_id} został usunięty z party '{party_data['party_name']}'.")

        if member_to_remove_obj:
            try:
                await member_to_remove_obj.send(
                    f"Zostałeś/aś usunięty/a z party '{party_data['party_name']}' przez lidera.")
            except disnake.Forbidden:
                pass

        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @remove_member_dm_command.error
    async def remove_member_dm_command_error_handler(self, ctx, error):
        bot_response_msg = None
        if isinstance(error, commands.MissingRequiredArgument):
            if error.param.name == 'member_identifier':
                bot_response_msg = await ctx.send(
                    f"Musisz podać @wzmiankę lub ID, np. `{config.DEFAULT_COMMAND_PREFIX}usun_czlonka @uzytkownik`.")
        elif isinstance(error, commands.PrivateMessageOnly):
            pass
        else:
            bot_response_msg = await ctx.send(f"Błąd w !usun_czlonka: {type(error).__name__}")
            print(f"BŁĄD w !usun_czlonka: {error}")
        if bot_response_msg:
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @commands.command(name="zmien_nazwe_party")
    @commands.dm_only()
    async def rename_party_dm_command(self, ctx: commands.Context, *, new_name: str):
        leader = ctx.author
        party_id_led_by_author = next(
            (pid for pid, pdata in active_parties.items() if pdata.get("leader_id") == leader.id), None)
        bot_response_msg = None

        if not party_id_led_by_author:
            bot_response_msg = await ctx.send("Nie jesteś liderem żadnego aktywnego party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        party_id = party_id_led_by_author
        party_data = active_parties.get(party_id)

        new_name_stripped = new_name.strip()
        if not new_name_stripped:
            bot_response_msg = await ctx.send("Nowa nazwa party nie może być pusta.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return
        if not (0 < len(new_name_stripped) <= config.MAX_PARTY_NAME_LENGTH):
            bot_response_msg = await ctx.send(f"Nazwa musi mieć od 1 do {config.MAX_PARTY_NAME_LENGTH} znaków.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg,
                                            delay=config.DM_MESSAGE_DELETE_DELAY * 1.5);
            return
        if new_name_stripped == party_data["party_name"]:
            bot_response_msg = await ctx.send(f"Nowa nazwa jest taka sama. Nie dokonano zmian.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        old_name = party_data["party_name"]
        party_data["party_name"] = new_name_stripped

        guild = self.bot.get_guild(party_data["guild_id"])
        leader_display_name_for_cat = leader.display_name
        if guild:
            leader_member_obj = guild.get_member(leader.id)
            if leader_member_obj:
                leader_display_name_for_cat = leader_member_obj.display_name

            category = guild.get_channel(party_data["category_id"]) if party_data.get("category_id") else None
            if category and isinstance(category, disnake.CategoryChannel):
                try:
                    await category.edit(name=f"🎉 {new_name_stripped} ({leader_display_name_for_cat})",
                                        reason=f"Zmiana nazwy party przez lidera {leader.id}")
                except disnake.HTTPException as e:
                    print(f"WARN: Zmiana nazwy kategorii dla party {party_id} nie powiodła się: {e}")

            channel_configs = [
                ("settings_channel_id", f"📌︱info-{new_name_stripped[:20]}"),
                ("text_channel_id", f"💬︱{new_name_stripped[:20]}"),
                ("voice_channel_id", f"🔊︱Głos 1 ({new_name_stripped[:15]})"),
                ("voice_channel_id_2", f"🔊︱Głos 2 ({new_name_stripped[:15]})")
            ]
            for ch_key, ch_new_name_format in channel_configs:
                ch_id = party_data.get(ch_key)
                if ch_id:
                    channel_obj = guild.get_channel(ch_id)
                    if channel_obj:
                        try:
                            await channel_obj.edit(name=ch_new_name_format,
                                                   reason=f"Zmiana nazwy party przez lidera {leader.id}")
                        except disnake.HTTPException as e:
                            print(f"WARN: Nie udało się zmienić nazwy kanału {ch_key} ({ch_id}): {e}")

        save_party_data()
        await self._update_party_emblem(party_id)
        if party_data.get("settings_channel_id"):
            await self._update_settings_embed(party_id)

        await self.send_leader_control_panel(leader, party_id)
        bot_response_msg = await ctx.send(f"Nazwa party zmieniona z '{old_name}' na '{new_name_stripped}'.")
        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @rename_party_dm_command.error
    async def rename_party_dm_command_error_handler(self, ctx, error):
        bot_response_msg = None
        if isinstance(error, commands.MissingRequiredArgument):
            if error.param.name == 'new_name':
                bot_response_msg = await ctx.send(
                    f"Podaj nową nazwę, np. `{config.DEFAULT_COMMAND_PREFIX}zmien_nazwe_party Moje Party`.")
        else:
            bot_response_msg = await ctx.send(f"Błąd w !zmien_nazwe_party: {type(error).__name__}")
            print(f"BŁĄD w !zmien_nazwe_party: {error}")
        if bot_response_msg:
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @commands.command(name="lista_czlonkow", aliases=["panel", "refreshpanel"])
    @commands.dm_only()
    async def list_members_dm_command(self, ctx: commands.Context):
        leader = ctx.author
        party_id_led_by_author = next(
            (pid for pid, pdata in active_parties.items() if pdata.get("leader_id") == leader.id), None)
        bot_response_msg = None

        if not party_id_led_by_author:
            bot_response_msg = await ctx.send("Nie jesteś liderem żadnego aktywnego party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        party_id = party_id_led_by_author
        await self.send_leader_control_panel(leader, party_id)
        bot_response_msg = await ctx.send("Panel zarządzania odświeżony.")
        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @tasks.loop(minutes=config.EXTENSION_CHECK_LOOP_MINUTES)
    async def extension_check_loop(self):
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        p_ids_iter = list(active_parties.keys())

        for p_id in p_ids_iter:
            p_data = active_parties.get(p_id)
            if not p_data: continue

            if now_ts >= p_data["expiry_timestamp"]:
                await self.disband_party(p_id,
                                         reason=f"Party automatycznie wygasło <t:{int(p_data['expiry_timestamp'])}:F>.")
                continue

            should_send_reminder = (
                    not p_data.get("reminder_sent_for_current_cycle", False) and
                    p_id not in parties_awaiting_extension_reply and
                    now_ts >= p_data.get("next_reminder_timestamp", float('inf')) and
                    p_data["expiry_timestamp"] > now_ts
            )

            if should_send_reminder:
                ldr = self.bot.get_user(p_data["leader_id"])
                if not ldr:
                    try:
                        ldr = await self.bot.fetch_user(p_data["leader_id"])
                    except (disnake.NotFound, disnake.HTTPException):
                        print(f"WARN LOOP: Lider party {p_id} nieosiągalny. Party wygaśnie normalnie.")
                        continue
                try:
                    reply_due_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                        hours=config.EXTENSION_WINDOW_HOURS)
                    reply_due_ts = reply_due_dt.timestamp()
                    dm_ch = await ldr.create_dm()
                    reminder_msg_content = (
                        f"🔔 Przypomnienie!\nTwoje party **'{p_data['party_name']}'** wygasa <t:{int(p_data['expiry_timestamp'])}:R>.\n"
                        f"Przedłużyć o **{config.PARTY_EXTEND_BY_HOURS}**h? Odpisz `Tak`/`Nie` do <t:{int(reply_due_ts)}:R>."
                    )
                    reminder_dm_msg = await dm_ch.send(reminder_msg_content)
                    parties_awaiting_extension_reply[p_id] = {
                        'reply_due_ts': reply_due_ts,
                        'leader_dm_channel_id': dm_ch.id,
                        'reminder_message_id': reminder_dm_msg.id
                    }
                    p_data["reminder_sent_for_current_cycle"] = True
                    p_data["extension_reminder_dm_id"] = reminder_dm_msg.id
                    save_party_data()
                    print(f"INFO LOOP: Wysłano przypomnienie o przedłużeniu do lidera party {p_id}.")
                except disnake.Forbidden:
                    print(f"WARN LOOP: Nie udało się wysłać DM z przypomnieniem do lidera {ldr.id} dla party {p_id}.")
                except Exception as e:
                    print(f"BŁĄD LOOP podczas wysyłania przypomnienia dla party {p_id}: {e}")

            if p_id in parties_awaiting_extension_reply and now_ts >= parties_awaiting_extension_reply[p_id][
                'reply_due_ts']:
                ldr = self.bot.get_user(p_data["leader_id"])
                if not ldr:
                    try:
                        ldr = await self.bot.fetch_user(p_data["leader_id"])
                    except:
                        pass

                reminder_info = parties_awaiting_extension_reply[p_id]
                if reminder_info.get('leader_dm_channel_id') and reminder_info.get('reminder_message_id'):
                    try:
                        dm_ch_for_cleanup = self.bot.get_channel(reminder_info['leader_dm_channel_id'])
                        if not dm_ch_for_cleanup:
                            dm_ch_for_cleanup = await self.bot.fetch_channel(reminder_info['leader_dm_channel_id'])
                        if isinstance(dm_ch_for_cleanup, disnake.DMChannel):
                            msg_to_delete = await dm_ch_for_cleanup.fetch_message(reminder_info['reminder_message_id'])
                            await msg_to_delete.delete()
                    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                        pass
                del parties_awaiting_extension_reply[p_id]
                if ldr:
                    try:
                        await ldr.send(
                            f"Nie otrzymano odpowiedzi ws. przedłużenia party '{p_data['party_name']}'. Wygasnie <t:{int(p_data['expiry_timestamp'])}:R>.",
                            delete_after=config.DM_MESSAGE_DELETE_DELAY * 2
                        )
                    except disnake.Forbidden:
                        pass
                print(f"INFO LOOP: Lider party {p_id} nie odpowiedział na czas. Party wygaśnie normalnie.")

    @extension_check_loop.before_loop
    async def before_extension_check_loop_func(self):
        await self.bot.wait_until_ready()
        print("Pętla sprawdzania przedłużeń party jest gotowa.")

    @commands.Cog.listener("on_message")
    async def on_extension_reply(self, message: disnake.Message):
        if message.author.bot or message.guild is not None: return

        author_id = message.author.id
        party_id_being_processed = None
        extension_data_for_party = None

        for pid, ext_data in list(parties_awaiting_extension_reply.items()):
            p_data_check = active_parties.get(pid)
            if p_data_check and \
                    p_data_check.get("leader_id") == author_id and \
                    ext_data.get('leader_dm_channel_id') == message.channel.id:
                if datetime.datetime.now(datetime.timezone.utc).timestamp() < ext_data['reply_due_ts']:
                    party_id_being_processed = pid
                    extension_data_for_party = ext_data
                    break
                else:
                    if ext_data.get('reminder_message_id'):
                        try:
                            msg_to_del = await message.channel.fetch_message(ext_data['reminder_message_id'])
                            await msg_to_del.delete()
                        except:
                            pass
                    try:
                        await message.delete()
                    except:
                        pass
                    try:
                        await message.channel.send(
                            f"Odpowiedź ('{message.content}') dla party '{p_data_check.get('party_name', 'N/A')}' przyszła po czasie.",
                            delete_after=config.DM_MESSAGE_DELETE_DELAY * 2)
                    except:
                        pass
                    del parties_awaiting_extension_reply[pid]
                    return

        if not party_id_being_processed or not extension_data_for_party: return

        p_data = active_parties.get(party_id_being_processed)
        if not p_data:
            if party_id_being_processed in parties_awaiting_extension_reply:
                del parties_awaiting_extension_reply[party_id_being_processed]
            return

        reply_content = message.content.strip().lower()
        bot_response_after_reply_msg = None
        user_reply_msg = message

        if extension_data_for_party.get('reminder_message_id'):
            try:
                original_reminder_msg = await message.channel.fetch_message(
                    extension_data_for_party['reminder_message_id'])
                await original_reminder_msg.delete()
            except:
                pass

        if reply_content == "tak":
            new_expiry_ts = p_data["expiry_timestamp"] + datetime.timedelta(
                hours=config.PARTY_EXTEND_BY_HOURS).total_seconds()
            p_data["expiry_timestamp"] = new_expiry_ts
            next_rem_ts_after_extend = new_expiry_ts - datetime.timedelta(
                hours=config.EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY).total_seconds()
            if config.PARTY_LIFESPAN_HOURS <= config.EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY:
                next_rem_ts_after_extend = new_expiry_ts
            p_data["next_reminder_timestamp"] = next_rem_ts_after_extend
            p_data["reminder_sent_for_current_cycle"] = False
            p_data["extension_reminder_dm_id"] = None
            del parties_awaiting_extension_reply[party_id_being_processed]
            save_party_data()
            bot_response_after_reply_msg = await message.channel.send(
                f"Party **'{p_data['party_name']}'** przedłużone! Nowy czas wygaśnięcia: <t:{int(new_expiry_ts)}:F>.")
            await self.send_leader_control_panel(message.author, party_id_being_processed)
            print(f"INFO REPLY: Party {party_id_being_processed} przedłużone przez lidera.")
        elif reply_content == "nie":
            p_data["extension_reminder_dm_id"] = None
            del parties_awaiting_extension_reply[party_id_being_processed]
            save_party_data()
            bot_response_after_reply_msg = await message.channel.send(
                f"Nie przedłużono party **'{p_data['party_name']}'**. Wygasnie <t:{int(p_data['expiry_timestamp'])}:R>.")
            print(f"INFO REPLY: Lider nie przedłużył party {party_id_being_processed}.")
        else:
            current_reply_due_ts = extension_data_for_party['reply_due_ts']
            new_reminder_content = (
                f"⚠️ Nieprawidłowa odpowiedź: '{message.content}'.\n"
                f"Party **'{p_data['party_name']}'** wygasa <t:{int(p_data['expiry_timestamp'])}:R>.\n"
                f"Przedłużyć o **{config.PARTY_EXTEND_BY_HOURS}**h? Odpisz `Tak`/`Nie` do <t:{int(current_reply_due_ts)}:R>."
            )
            try:
                new_reminder_msg = await message.channel.send(new_reminder_content)
                parties_awaiting_extension_reply[party_id_being_processed]['reminder_message_id'] = new_reminder_msg.id
                p_data["extension_reminder_dm_id"] = new_reminder_msg.id
                save_party_data()
                bot_response_after_reply_msg = None
            except disnake.HTTPException as e:
                print(
                    f"BŁĄD REPLY: Nie udało się wysłać ponownego przypomnienia dla party {party_id_being_processed}: {e}")
            user_reply_msg = message
        await self._cleanup_dm_messages(None, bot_message=bot_response_after_reply_msg, user_message=user_reply_msg)


def setup(bot: commands.Bot):
    cog_instance = PartyManagementCog(bot)
    bot.add_cog(cog_instance)
    print(f"Cog '{cog_instance.qualified_name}' został pomyślnie załadowany i dodany do bota.")