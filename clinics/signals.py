import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import Clinic, ProvisioningStatus
from .provisioning import provision_clinic_database, deprovision_clinic_database
from .tasks import sync_modules_to_edge

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Clinic)
def provision_on_key_received(sender, instance, **kwargs):
    """
    Provisiona o banco quando a clínica recebe a chave pública do app desktop.
    Disparado tanto na criação quanto na atualização do registro.
    """
    if instance.provisioning_status != ProvisioningStatus.WAITING_KEY:
        return
    if not instance.public_key_pem:
        return

    try:
        db_name, db_user, encrypted = provision_clinic_database(instance)
        Clinic.objects.filter(pk=instance.pk).update(
            db_name=db_name,
            db_user=db_user,
            db_password_encrypted=encrypted,
            provisioning_status=ProvisioningStatus.PROVISIONED,
            provisioning_error='',
        )
        logger.info("clinic_provisioned clinic_id=%s db=%s", str(instance.id), db_name)
    except (RuntimeError, Exception) as exc:
        Clinic.objects.filter(pk=instance.pk).update(
            provisioning_status=ProvisioningStatus.FAILED,
            provisioning_error='provisioning_failed',
        )
        logger.error(
            "clinic_provisioning_error clinic_id=%s exc_type=%s",
            str(instance.id),
            type(exc).__name__,
        )


@receiver(post_delete, sender=Clinic)
def deprovision_on_delete(sender, instance, **kwargs):
    if not instance.db_name or not instance.db_user:
        return
    try:
        deprovision_clinic_database(instance.db_name, instance.db_user)
        logger.info("clinic_deprovisioned db=%s", instance.db_name)
    except RuntimeError:
        logger.error("clinic_deprovisioning_error db=%s", instance.db_name)


@receiver(post_save, sender=Clinic)
def sync_modules_on_save(sender, instance, created, **kwargs):
    """
    Sincroniza os módulos com o Edge sempre que a clínica é alterada.
    Ignora se a atualização foi apenas em campos internos de provisionamento
    ou se a clínica não tem gateway_url.
    """
    update_fields = kwargs.get('update_fields')
    if update_fields:
        prov_fields = {
            'provisioning_status', 'provisioning_error',
            'db_name', 'db_user', 'db_password_encrypted',
            'public_key_pem'
        }
        # Se todos os campos alterados forem de provisionamento, ignora
        if set(update_fields).issubset(prov_fields):
            return

    if instance.gateway_url:
        sync_modules_to_edge.delay(str(instance.pk))

