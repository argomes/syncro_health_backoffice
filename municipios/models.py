import unicodedata

from django.db import models


def normalizar_texto(texto: str) -> str:
    """
    Remove acentos e caixa para permitir busca tolerante a acentuação.

    Achado real na revisão do BACFF-015: `nome__icontains` sozinho falha
    para a maioria das buscas reais, porque o usuário brasileiro comum
    digita "sao paulo" sem acento num campo de busca rápida — o endpoint
    de autocomplete retornava lista vazia (200 OK, sem erro) para esse
    caso, que é o caminho principal de uso, não uma borda rara.
    """
    if not texto:
        return ""
    forma_decomposta = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in forma_decomposta if not unicodedata.combining(c))
    return sem_acento.lower()


class Municipio(models.Model):
    """
    Lista central de municípios brasileiros (BACFF-015).

    Fonte central única para todo o backoffice — pensada para ser
    compartilhada também pela API de feriados (`holidays`), evitando duas
    bases de município divergentes no mesmo sistema (ver reasoning da
    task no tracker de tasks do projeto).

    Dado público, não-PII: é referência geográfica do IBGE, não dado de
    paciente/clínica. Não há isolamento multi-tenant aqui de propósito —
    é uma lista global, igual para todas as clínicas, sem qualquer FK
    para `Clinic`.
    """

    codigo_ibge = models.CharField(
        max_length=7,
        unique=True,
        db_index=True,
        help_text="Código IBGE do município, 7 dígitos.",
    )
    nome = models.CharField(max_length=150, db_index=True)
    uf = models.CharField(max_length=2, db_index=True)
    nome_normalizado = models.CharField(
        max_length=150,
        db_index=True,
        blank=True,
        help_text="`nome` sem acento e em minúsculas — mantido em sync via save(), usado só para busca tolerante a acentuação.",
    )

    class Meta:
        ordering = ['nome']
        indexes = [
            models.Index(fields=['nome', 'uf']),
        ]

    def save(self, *args, **kwargs):
        self.nome_normalizado = normalizar_texto(self.nome)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nome}/{self.uf} ({self.codigo_ibge})"
