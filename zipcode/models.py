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


class Cep(models.Model):
    """
    Lista central de CEPs brasileiros (BACFF-015).

    Fonte central única para todo o backoffice — pensada para ser
    compartilhada também pela API de feriados (`holidays`), evitando duas
    bases de CEP divergentes no mesmo sistema (ver reasoning da
    task no tracker de tasks do projeto).

    Dado público, não-PII: é referência geográfica do IBGE, não dado de
    paciente/clínica. Não há isolamento multi-tenant aqui de propósito —
    é uma lista global, igual para todas as clínicas, sem qualquer FK
    para `Clinic`.
    """

    codigo_ibge = models.CharField(
        max_length=7,
        db_index=True,
        help_text=(
            "Código IBGE do município, 7 dígitos. NÃO é único: um mesmo "
            "município tem muitos CEPs (linhas) apontando para o mesmo "
            "`codigo_ibge` — a unicidade real desta tabela é por `cep`."
        ),
    )
    
    cep = models.CharField(
        max_length=9,
        unique=True,
        db_index=True,
        help_text="CEP do município, 8 dígitos + hífen opcional.",
    )
    
    logradouro = models.CharField(
        max_length=250,
        blank=True,
        help_text="Logradouro do CEP, se houver (ex.: 'Rua das Flores').",
    )
    
    bairro = models.CharField(
        max_length=250,
        blank=True,
        help_text="Bairro do CEP, se houver (ex.: 'Jardim das Acácias').",
    )
    
    localidade = models.CharField(
        max_length=250,
        blank=True,
        help_text="Localidade do CEP, se houver (ex.: 'São Paulo').",
    )
    
    estado = models.CharField(
        max_length=250,
        blank=True,
        help_text="Estado do CEP, se houver (ex.: 'São Paulo').",
    )
    
    regiao = models.CharField(
        max_length=250,
        blank=True,
        help_text="Região do CEP, se houver (ex.: 'Sudeste').",
    )
    
    
    uf = models.CharField(max_length=2, db_index=True)
    logradouro_normalizado = models.CharField(
        max_length=250,
        db_index=True,
        blank=True,
        help_text="`logradouro` sem acento e em minúsculas — mantido em sync via save(), usado só para busca tolerante a acentuação.",
    )
    
    estado_normalizado = models.CharField(
        max_length=250,
        db_index=True,
        blank=True,
        help_text="`estado` sem acento e em minúsculas — mantido em sync via save(), usado só para busca tolerante a acentuação.",
    )
    
    cidade_normalizada = models.CharField(
        max_length=250,
        db_index=True,
        blank=True,
        help_text="`localidade` sem acento e em minúsculas — mantido em sync via save(), usado só para busca tolerante a acentuação.",
    )   
    
    gia = models.CharField(
        max_length=4,
        blank=True,
        help_text="Código GIA do município, se houver (ex.: '1004').",
    )
    
    ddd = models.CharField(
        max_length=2,
        blank=True,
        help_text="DDD do município, se houver (ex.: '11').",
    )
    
    siafi = models.CharField(
        max_length=4,
        blank=True,
        help_text="Código SIAFI do município, se houver (ex.: '7107').",
    )
    

    class Meta:
        ordering = ['cep']
        indexes = [
            models.Index(fields=['cep', 'uf']),
        ]

    def save(self, *args, **kwargs):
        self.logradouro_normalizado = normalizar_texto(self.logradouro)
        self.estado_normalizado = normalizar_texto(self.estado)
        self.cidade_normalizada = normalizar_texto(self.localidade)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cep}/{self.uf} ({self.codigo_ibge})"
