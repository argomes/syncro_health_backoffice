from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from clinics.views import issue_service_token, get_license_info
from billing.webhook_views import asaas_webhook
from syncro_backoffice.throttling import LoginRateThrottle

class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/login/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # API v1 contracts
    path('api/v1/auth/service-token/', issue_service_token, name='issue_service_token'),
    path('api/v1/license/', get_license_info, name='get_license_info'),
    path('api/v1/billing/webhook/asaas/', asaas_webhook, name='asaas_webhook'),
    
    path('api/clinics/', include('clinics.urls')),
    path('api/billing/', include('billing.urls')),
    path('api/metrics/', include('metrics.urls')),
    path('api/accounts/', include('accounts.urls')),
    path('api/support/', include('support.urls')),
]
