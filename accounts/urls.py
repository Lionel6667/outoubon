from django.urls import path
from . import views
from . import payments

urlpatterns = [
    path('',                  views.landing,              name='landing'),
    path('login/',            views.login_view,           name='login'),
    path('signup/',           views.signup_view,          name='signup'),
    path('signup/step2/',     views.signup_step2_view,    name='signup_step2'),
    path('logout/',           views.logout_view,          name='logout'),
    path('diagnostic/',          views.diagnostic_view,          name='diagnostic'),
    path('diagnostic/generate/', views.diagnostic_generate_view,  name='diagnostic_generate'),
    path('complete-profile/',    views.complete_profile_view,     name='complete_profile'),
    path('schools/',          views.school_search_view,   name='school_search'),
    # Session persistence APIs
    path('api/auth/token/get/',    views.get_auth_token_view,     name='get_auth_token'),
    path('api/auth/token/verify/', views.verify_auth_token_view,   name='verify_auth_token'),
    # Device fingerprint
    path('api/device/check/',      views.api_device_check,         name='api_device_check'),
    path('api/device/status/',     views.api_device_status,        name='api_device_status'),
    # Paiement MonCash
    path('pricing/',               payments.pricing_view,          name='pricing'),
    path('create-payment/',        payments.create_payment,        name='create_payment'),
    path('payment-success/',       payments.payment_success,       name='payment_success'),
    path('payment-status/',        payments.check_payment_status,  name='check_payment_status'),
    path('webhook/peyem/',         payments.peyem_webhook,         name='peyem_webhook'),
    # Cadeau — demander à un proche de payer
    path('cadeau/',                    payments.generate_gift_link,       name='gift_generate'),
    path('cadeau/<str:token>/',        payments.gift_payment_page,        name='gift_pay'),
    path('cadeau/<str:token>/payer/',  payments.create_gift_payment,      name='gift_create_payment'),
    path('cadeau/<str:token>/merci/',  payments.gift_success_page,        name='gift_success'),
    path('gift-payment-status/',       payments.check_gift_payment_status,name='gift_payment_status'),
    # Agent
    path('agent/login/',      views.agent_login_view,     name='agent_login'),
    path('agent/register/',   views.agent_register_view,  name='agent_register'),
    path('agent/dashboard/',  views.agent_dashboard_view, name='agent_dashboard'),
    path('agent/api/withdrawal-status/', views.agent_withdrawal_status_api, name='agent_withdrawal_status'),
]
