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

    