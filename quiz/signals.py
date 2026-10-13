from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    # raw=True при loaddata: профили приходят из дампа, создавать их не нужно.
    if created and not kwargs.get("raw", False):
        UserProfile.objects.get_or_create(user=instance)
