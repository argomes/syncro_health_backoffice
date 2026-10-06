from django.db import models


class Feriado(models.Model):
    TYPE_CHOICES = [
        ('NACIONAL', 'Nacional'),
        ('ESTADUAL', 'Estadual'),
        ('MUNICIPAL', 'Municipal'),
    ]
      
    date = models.DateField()
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    uf = models.CharField(max_length=2, null=True, blank=True)
    ibge_code = models.CharField(max_length=7, null=True, blank=True, db_index=True)
    year = models.IntegerField(db_index=True)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('date', 'type', 'ibge_code')
        ordering = ['date']
    
    def __str__(self):
        return f"{self.date} - {self.name} ({self.get_type_display()})"

    

class FeriadoBusca(models.Model):
    """Registro de controle: "já buscamos o calendário deste ibge/ano na API".

    Por que existe: a existência de `Feriado` não serve como marcador de
    cache. Município sem feriado municipal próprio nunca tem linha com o
    seu `ibge_code`, então o critério antigo (`Feriado(ibge_code, year)
    exists`) rebatia a API paga em TODA chamada. Uma linha por par
    ibge/ano, gravada só quando a busca veio completa, resolve isso com
    uma tabela minúscula e sem tocar no schema de `Feriado`.

    Dado público (calendário oficial), sem PII e sem vínculo com tenant.
    """

    ibge_code = models.CharField(max_length=7)
    year = models.IntegerField()
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['ibge_code', 'year'], name='uniq_feriado_busca_ibge_year'),
        ]
        verbose_name = 'Busca de feriados'
        verbose_name_plural = 'Buscas de feriados'

    def __str__(self) -> str:
        return f"{self.ibge_code}/{self.year}"
