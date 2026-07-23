import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from municipios.models import Municipio


class Command(BaseCommand):
    """
    Importa a lista de municípios brasileiros (IBGE + nome + UF) para a
    tabela `Municipio` (BACFF-015).

    O repositório já traz uma cópia da lista oficial em
    `municipios/data/ibge_municipios.json` (5571 municípios, obtida da
    API pública do IBGE — https://servicodados.ibge.gov.br/api/v1/localidades/municipios),
    então o uso normal é sem argumento:

        python manage.py import_municipios

    Também aceita um arquivo alternativo (mesmo formato: lista de objetos
    com `codigo_ibge`, `nome`, `uf`), por exemplo se o dataset do IBGE for
    atualizado:

        python manage.py import_municipios /caminho/para/outro.json

    Carregamento é feito via `update_or_create` por `codigo_ibge` — pode
    ser rodado de novo com segurança (idempotente).
    """

    help = "Importa municípios brasileiros (IBGE + nome + UF) a partir de um arquivo JSON."

    default_data_file = "municipios/data/ibge_municipios.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "arquivo",
            nargs="?",
            default=None,
            help="Caminho para o JSON de municípios. Se omitido, usa o dataset padrão do repositório.",
        )

    def handle(self, *args, **options):
        path = options["arquivo"] or self.default_data_file

        try:
            with open(path, encoding="utf-8") as f:
                registros = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"Arquivo não encontrado: {path}")
        except json.JSONDecodeError as exc:
            raise CommandError(f"JSON inválido em {path}: {exc}")

        if not isinstance(registros, list):
            raise CommandError("Formato inválido: esperada uma lista de objetos {codigo_ibge, nome, uf}.")

        criados = 0
        atualizados = 0
        erros = []

        with transaction.atomic():
            for i, registro in enumerate(registros):
                try:
                    codigo_ibge = str(registro["codigo_ibge"]).strip()
                    nome = str(registro["nome"]).strip()
                    uf = str(registro["uf"]).strip().upper()
                except (KeyError, AttributeError):
                    erros.append(f"registro {i}: campos obrigatórios ausentes (codigo_ibge, nome, uf)")
                    continue

                if not codigo_ibge or not nome or len(uf) != 2:
                    erros.append(f"registro {i}: dados inválidos ({registro!r})")
                    continue

                _, created = Municipio.objects.update_or_create(
                    codigo_ibge=codigo_ibge,
                    defaults={"nome": nome, "uf": uf},
                )
                if created:
                    criados += 1
                else:
                    atualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"Importação concluída: {criados} criados, {atualizados} atualizados, {len(erros)} erros."
        ))
        for erro in erros[:20]:
            self.stdout.write(self.style.WARNING(erro))
        if len(erros) > 20:
            self.stdout.write(self.style.WARNING(f"... e mais {len(erros) - 20} erros."))
