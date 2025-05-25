# party_bot/cogs/party_creation_flow.py

import disnake
import asyncio
# Zakładamy, że config jest importowany w plikach, które bezpośrednio go potrzebują,
# lub jest przekazywany. Tutaj użyjemy względnego importu,
# zakładając, że ten plik jest częścią pakietu 'cogs'.
import config


async def handle_game_selection_dm(bot: disnake.Client, user: disnake.User,
                                   dm_channel: disnake.DMChannel) -> str | None:
    """Obsługuje wybór gry przez użytkownika w DM, z poprawnym usuwaniem wiadomości."""
    bot_game_prompt_msg = None
    try:
        game_selection_message_content = "Wybierz Grę:\n" + "\n".join(
            [f"{emoji} - {game}" for game, emoji in config.GAMES_EMOJI_SELECT.items()]
        )
        bot_game_prompt_msg = await dm_channel.send(game_selection_message_content)
        for emoji_to_add in config.GAMES_EMOJI_SELECT.values():
            await bot_game_prompt_msg.add_reaction(emoji_to_add)

        def game_check(reaction_arg: disnake.Reaction, user_arg: disnake.User):
            return user_arg.id == user.id and \
                reaction_arg.message.id == bot_game_prompt_msg.id and \
                str(reaction_arg.emoji) in config.EMOJI_TO_GAME_SELECT

        reaction, _ = await bot.wait_for('reaction_add', timeout=180.0, check=game_check)
        selected_game = config.EMOJI_TO_GAME_SELECT[str(reaction.emoji)]

        # Usuń prompt bota po udanej reakcji
        try:
            await bot_game_prompt_msg.delete()
        except disnake.HTTPException:
            pass
        bot_game_prompt_msg = None  # Aby finally go nie próbowało ponownie usunąć

        # Można wysłać krótkie potwierdzenie, które też zniknie
        # confirm_msg = await dm_channel.send(f"Gra: {selected_game}", delete_after=config.DM_MESSAGE_DELETE_DELAY)
        return selected_game

    except asyncio.TimeoutError:
        if bot_game_prompt_msg:
            try:
                await bot_game_prompt_msg.delete()
            except disnake.HTTPException:
                pass
        await dm_channel.send("Anulowano tworzenie party (brak wyboru gry w ciągu 3 minut).")
        return None
    except disnake.Forbidden:
        print(f"DM ERR: Nie można wysłać wiadomości lub dodać reakcji do {user.name} ({user.id}) podczas wyboru gry.")
        if bot_game_prompt_msg:
            try:
                await bot_game_prompt_msg.delete()
            except disnake.HTTPException:
                pass
        return None
    except Exception as e:
        print(f"ERR: Nieoczekiwany błąd w handle_game_selection_dm: {e}")
        if bot_game_prompt_msg:
            try:
                await bot_game_prompt_msg.delete()
            except disnake.HTTPException:
                pass
        await dm_channel.send(f"Wystąpił błąd podczas wyboru gry: {type(e).__name__}")
        return None
    finally:  # Dodatkowe zabezpieczenie, jeśli błąd wystąpił przed nullowaniem promptu
        if bot_game_prompt_msg:
            try:
                await bot_game_prompt_msg.delete()
            except disnake.HTTPException:
                pass


async def handle_party_name_dm(bot: disnake.Client, user: disnake.User, dm_channel: disnake.DMChannel) -> str | None:
    """Obsługuje podanie nazwy party przez użytkownika w DM, z poprawnym usuwaniem wiadomości."""
    bot_prompt_msg_obj = None
    user_response_msg_obj = None

    while True:
        try:
            bot_prompt_msg_obj = await dm_channel.send(f"Podaj nazwę Party (1-{config.MAX_PARTY_NAME_LENGTH} znaków):")

            def name_check(message_arg: disnake.Message):
                return message_arg.author.id == user.id and \
                    message_arg.channel.id == dm_channel.id

            user_response_msg_obj = await bot.wait_for('message', timeout=180.0, check=name_check)

            # 1. Pobierz treść ZANIM usuniesz obiekt wiadomości lub ustawisz go na None
            if user_response_msg_obj is None:  # Dodatkowe zabezpieczenie, choć wait_for powinno rzucić wyjątek
                raise ValueError("bot.wait_for('message') zwróciło None bez wyjątku.")

            party_name_input = user_response_msg_obj.content.strip()

            # 2. Usuń wiadomości (prompt bota i odpowiedź usera)
            if bot_prompt_msg_obj:
                try:
                    await bot_prompt_msg_obj.delete()
                except disnake.HTTPException:
                    pass

            # user_response_msg_obj jest tutaj na pewno obiektem Message
            try:
                await user_response_msg_obj.delete()
            except disnake.HTTPException:
                pass

            # Ustaw obiekty na None PO ich usunięciu, aby finally nie próbowało ponownie
            # i aby w następnej iteracji pętli (jeśli walidacja nazwy się nie powiedzie)
            # były one czyste.
            bot_prompt_msg_obj = None
            user_response_msg_obj = None

            # 3. Walidacja i zwrot wartości
            if 0 < len(party_name_input) <= config.MAX_PARTY_NAME_LENGTH:
                # Można wysłać krótkie potwierdzenie, które też zniknie
                # confirm_msg = await dm_channel.send(f"Nazwa: {party_name_input}", delete_after=config.DM_MESSAGE_DELETE_DELAY)
                return party_name_input  # Sukces, wychodzimy z funkcji
            else:
                error_feedback_msg = await dm_channel.send(
                    f"Nieprawidłowa nazwa. Nazwa musi mieć od 1 do {config.MAX_PARTY_NAME_LENGTH} znaków. Spróbuj ponownie."
                )
                await asyncio.sleep(config.DM_MESSAGE_DELETE_DELAY - 2)  # Daj czas na przeczytanie
                try:
                    await error_feedback_msg.delete()
                except disnake.HTTPException:
                    pass
                # Pętla while True będzie kontynuowana, bot_prompt_msg_obj i user_response_msg_obj są None

        except asyncio.TimeoutError:
            if bot_prompt_msg_obj:
                try:
                    await bot_prompt_msg_obj.delete()
                except disnake.HTTPException:
                    pass
            await dm_channel.send("Anulowano tworzenie party (brak podania nazwy w ciągu 3 minut).")
            return None
        except disnake.Forbidden:
            print(f"DM ERR: Nie można wysłać wiadomości do {user.name} ({user.id}) podczas podawania nazwy party.")
            if bot_prompt_msg_obj:
                try:
                    await bot_prompt_msg_obj.delete()
                except disnake.HTTPException:
                    pass
            return None
        except Exception as e:  # Łapie też potencjalny ValueError z góry
            print(f"ERR: Nieoczekiwany błąd w handle_party_name_dm: {e} (Typ: {type(e)})")
            if bot_prompt_msg_obj:
                try:
                    await bot_prompt_msg_obj.delete()
                except disnake.HTTPException:
                    pass
            # Wyślij użytkownikowi informację o błędzie, ale użyj type(e).__name__ zamiast całego obiektu błędu
            await dm_channel.send(f"Wystąpił nieoczekiwany błąd podczas podawania nazwy party: {type(e).__name__}")
            return None
        # Blok finally nie jest już tak krytyczny, ponieważ staramy się ustawiać wiadomości na None
        # po ich przetworzeniu i usunięciu w bloku try.