from django.db import models


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

    class Meta:
        ordering = ['nome']
        indexes = [
            models.Index(fields=['nome', 'uf']),
        ]

    def __str__(self):
        return f"{self.nome}/{self.uf} ({self.codigo_ibge})"
