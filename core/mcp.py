"""Administrative MCP tools protected by Django administrator access."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models.functions import Coalesce
from django.utils import timezone
from mcp_server import MCPToolset


# This project is single-tenant. Keep the generic engine ready for an eventual
# required tenant foreign key without treating the singleton Company as one.
TENANT_FK = None

# English, snake_case slug -> every concrete model declared by a local app.
ENTITIES = {
    'action_permission': 'accounts.ActionPermission',
    'audit_log': 'core.AuditLog',
    'cash_account': 'billing.CashAccount',
    'category': 'catalog.Category',
    'company': 'company.Company',
    'customer': 'customers.Customer',
    'customer_message': 'notifications.CustomerMessage',
    'financial_movement': 'billing.FinancialMovement',
    'module_permission': 'accounts.ModulePermission',
    'payment': 'billing.Payment',
    'pickup': 'movements.Pickup',
    'product': 'catalog.Product',
    'receivable': 'billing.Receivable',
    'receipt': 'billing.Receipt',
    'receipt_allocation': 'billing.ReceiptAllocation',
    'rental': 'rentals.Rental',
    'rental_item': 'rentals.RentalItem',
    'return_record': 'movements.Return',
    'user': 'accounts.User',
}

SENSITIVE_FIELDS = {'password'}


class AdminMCPToolset(MCPToolset):
    """Administrative tools; every public method requires a Django admin."""

    # -- Authentication / authorization ---------------------------------
    def _user(self):
        user = getattr(self.request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            raise PermissionDenied('Autenticação obrigatória para usar o MCP.')
        return user

    def _require_admin(self):
        user = self._user()
        if not (user.is_staff or user.is_superuser):
            raise PermissionDenied(
                'Apenas administradores do Django podem usar o MCP.'
            )
        return user

    # -- Model / record helpers -----------------------------------------
    @staticmethod
    def _user_model():
        return get_user_model()

    @staticmethod
    def _audit_model():
        return apps.get_model('core.AuditLog')

    def _entity(self, entity):
        label = ENTITIES.get((entity or '').strip().lower())
        if not label:
            available = ', '.join(sorted(ENTITIES))
            raise ValueError(
                f"Entidade '{entity}' desconhecida. Use: {available}."
            )
        return apps.get_model(label)

    @staticmethod
    def _is_tenant_aware(model):
        if not TENANT_FK:
            return False
        for field in model._meta.concrete_fields:
            if field.name == TENANT_FK and field.is_relation:
                return not field.null
        return False

    @staticmethod
    def _relations(model):
        return [
            field.name
            for field in model._meta.concrete_fields
            if field.is_relation
        ]

    @staticmethod
    def _clamp(value, default=50, maximum=200):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = default
        return max(1, min(value or default, maximum))

    @staticmethod
    def _percentage(numerator, denominator):
        if not denominator:
            return 0.0
        return round(float(numerator) * 100 / float(denominator), 2)

    def _to_jsonable(self, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (list, tuple)):
            return [self._to_jsonable(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self._to_jsonable(item)
                for key, item in value.items()
            }
        return str(value)

    def _serialize(self, obj):
        data = {}
        for field in obj._meta.concrete_fields:
            if field.name in SENSITIVE_FIELDS:
                continue
            if field.is_relation:
                related_id = getattr(obj, field.attname)
                if related_id is None:
                    data[field.name] = None
                else:
                    related = getattr(obj, field.name, None)
                    data[field.name] = {
                        'id': related_id,
                        'label': str(related) if related else None,
                    }
            elif field.get_internal_type() in ('FileField', 'ImageField'):
                value = getattr(obj, field.attname)
                data[field.name] = str(value) if value else None
            else:
                data[field.name] = self._to_jsonable(
                    getattr(obj, field.attname)
                )
        for field in obj._meta.many_to_many:
            data[field.name] = [
                {'id': related.pk, 'label': str(related)}
                for related in getattr(obj, field.name).all()
            ]
        data['_label'] = str(obj)
        return data

    def _field_specs(self, model):
        fields = [*model._meta.concrete_fields, *model._meta.many_to_many]
        specs = []
        for field in fields:
            if field.name in SENSITIVE_FIELDS:
                continue
            spec = {
                'name': field.name,
                'type': field.get_internal_type(),
                'editable': bool(field.editable),
                'required': bool(
                    field.editable
                    and not field.blank
                    and not field.has_default()
                    and not field.primary_key
                ),
                'nullable': bool(field.null),
            }
            if field.is_relation and field.related_model is not None:
                spec['relation_to'] = field.related_model._meta.label
                spec['input'] = (
                    'list of ids'
                    if field.many_to_many
                    else f'{field.name}_id'
                )
            choices = getattr(field, 'choices', None)
            if choices:
                spec['choices'] = [
                    choice[0]
                    for choice in choices
                    if not isinstance(choice[1], (list, tuple))
                ]
            specs.append(spec)
        if model is self._user_model():
            specs.append({
                'name': 'password',
                'type': 'Password',
                'editable': True,
                'required': False,
                'nullable': False,
                'write_only': True,
            })
        return specs

    @staticmethod
    def _get_object(model, pk):
        try:
            return model._default_manager.get(pk=pk)
        except model.DoesNotExist as exc:
            raise ValueError(
                f'{model.__name__} com id={pk} não encontrado.'
            ) from exc

    def _scope(self, queryset, model, tenant_id):
        if not tenant_id or not self._is_tenant_aware(model):
            return queryset
        return queryset.filter(**{f'{TENANT_FK}_id': tenant_id})

    @staticmethod
    def _clean_filters(model, filters):
        names = {field.name for field in model._meta.get_fields()}
        names.update(field.attname for field in model._meta.concrete_fields)
        cleaned = {}
        for key, value in (filters or {}).items():
            if key.split('__', 1)[0] not in names:
                raise ValueError(
                    f"Filtro '{key}' inválido para {model.__name__}."
                )
            cleaned[key] = value
        return cleaned

    @staticmethod
    def _search_q(model, term):
        text_types = {'CharField', 'TextField', 'EmailField', 'SlugField'}
        query = Q()
        for field in model._meta.concrete_fields:
            if (
                field.get_internal_type() in text_types
                and not field.is_relation
                and field.name not in SENSITIVE_FIELDS
            ):
                query |= Q(**{f'{field.name}__icontains': term})
        return query

    @staticmethod
    def _field_maps(model):
        concrete = {field.name: field for field in model._meta.concrete_fields}
        many_to_many = {
            field.name: field for field in model._meta.many_to_many
        }
        return concrete, many_to_many

    def _apply_data(self, model, data, instance=None):
        if not isinstance(data, dict):
            raise ValueError("O parâmetro 'data' deve ser um objeto.")
        concrete, many_to_many = self._field_maps(model)
        obj = instance if instance is not None else model()
        pending_many_to_many = {}
        for key, value in data.items():
            name = (
                key[:-3]
                if key.endswith('_id') and key[:-3] in concrete
                else key
            )
            field = concrete.get(name)
            if field is not None:
                if not field.editable or field.primary_key:
                    raise ValueError(
                        f"Campo '{key}' inválido em {model.__name__}."
                    )
                target = field.attname if field.is_relation else field.name
                setattr(obj, target, value)
                continue
            field = many_to_many.get(name)
            if field is not None and field.editable:
                if not isinstance(value, (list, tuple)):
                    raise ValueError(
                        f"Campo '{key}' deve receber uma lista de ids."
                    )
                pending_many_to_many[name] = list(value)
                continue
            raise ValueError(f"Campo '{key}' inválido em {model.__name__}.")
        return obj, pending_many_to_many

    @staticmethod
    def _apply_many_to_many(obj, values):
        for field_name, related_ids in values.items():
            field = obj._meta.get_field(field_name)
            expected_ids = {str(related_id) for related_id in related_ids}
            existing_ids = {
                str(related_id)
                for related_id in field.related_model._default_manager.filter(
                    pk__in=related_ids
                ).values_list('pk', flat=True)
            }
            missing_ids = sorted(expected_ids - existing_ids)
            if missing_ids:
                raise ValueError(
                    f"Ids inexistentes em '{field_name}': "
                    f"{', '.join(missing_ids)}."
                )
            getattr(obj, field_name).set(related_ids)

    @staticmethod
    def _format_validation_error(exc):
        if hasattr(exc, 'message_dict'):
            parts = [
                f"{key}: {', '.join(map(str, messages))}"
                for key, messages in exc.message_dict.items()
            ]
            return 'Validação falhou — ' + '; '.join(parts)
        messages = getattr(exc, 'messages', [str(exc)])
        return 'Validação falhou — ' + '; '.join(map(str, messages))

    @staticmethod
    def _sum(queryset, field):
        result = queryset.aggregate(
            total=Coalesce(
                Sum(field),
                Value(Decimal('0')),
                output_field=DecimalField(),
            )
        )['total']
        return float(result or 0)

    def _scoped(self, label, tenant_id):
        model = apps.get_model(label)
        queryset = model._default_manager.all()
        if tenant_id and self._is_tenant_aware(model):
            queryset = queryset.filter(**{f'{TENANT_FK}_id': tenant_id})
        return queryset

    def _record_audit(self, action, obj, metadata=None):
        if obj._meta.label == 'core.AuditLog':
            return
        self._audit_model().record(
            user=self._user(),
            action=action,
            obj=obj,
            metadata=metadata or {},
        )

    @staticmethod
    def _validate_delete(model, obj):
        if model._meta.label != 'rentals.Rental':
            return
        payment_model = apps.get_model('billing.Payment')
        pickup_model = apps.get_model('movements.Pickup')
        return_model = apps.get_model('movements.Return')
        has_history = (
            pickup_model.objects.filter(rental=obj).exists()
            or return_model.objects.filter(rental=obj).exists()
            or payment_model.objects.filter(receivable__rental=obj).exists()
        )
        if has_history:
            raise ValueError(
                'A locação possui retirada, devolução ou pagamento e deve ser '
                'preservada para auditoria.'
            )
        if obj.status != obj.Status.CANCELLED:
            raise ValueError(
                'Cancele a locação antes da exclusão física.'
            )

    # -- Catalog ---------------------------------------------------------
    def list_entities(self) -> list[dict]:
        """List editable entity slugs, models, scope and record counts."""
        self._require_admin()
        result = []
        for slug, label in sorted(ENTITIES.items()):
            model = apps.get_model(label)
            result.append({
                'entity': slug,
                'model': model._meta.label,
                'tenant_aware': self._is_tenant_aware(model),
                'total_records': model._default_manager.count(),
            })
        return result

    def describe_entity(self, entity: str) -> dict:
        """Describe fields, choices and relations accepted by an entity."""
        self._require_admin()
        model = self._entity(entity)
        return {
            'entity': entity,
            'model': model._meta.label,
            'tenant_aware': self._is_tenant_aware(model),
            'fields': self._field_specs(model),
        }

    # -- Generic CRUD ----------------------------------------------------
    def list_records(
        self,
        entity: str,
        tenant_id: int | None = None,
        search: str | None = None,
        filters: dict | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List records with text search, ORM filters and pagination."""
        self._require_admin()
        model = self._entity(entity)
        queryset = model._default_manager.all()
        relations = self._relations(model)
        if relations:
            queryset = queryset.select_related(*relations)
        queryset = self._scope(queryset, model, tenant_id)
        if filters:
            queryset = queryset.filter(**self._clean_filters(model, filters))
        if search:
            queryset = queryset.filter(self._search_q(model, search))
        total = queryset.count()
        limit = self._clamp(limit)
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("O parâmetro 'offset' deve ser um inteiro.") from exc
        records = [
            self._serialize(obj)
            for obj in queryset.order_by('-pk')[offset:offset + limit]
        ]
        return {
            'entity': entity,
            'total': total,
            'offset': offset,
            'limit': limit,
            'records': records,
        }

    def get_record(self, entity: str, id: int) -> dict:
        """Return one complete record by primary key."""
        self._require_admin()
        return self._serialize(self._get_object(self._entity(entity), id))

    def count_records(
        self,
        entity: str,
        tenant_id: int | None = None,
        filters: dict | None = None,
    ) -> dict:
        """Count records with optional ORM filters and tenant scope."""
        self._require_admin()
        model = self._entity(entity)
        queryset = self._scope(
            model._default_manager.all(), model, tenant_id
        )
        if filters:
            queryset = queryset.filter(**self._clean_filters(model, filters))
        return {'entity': entity, 'total': queryset.count()}

    def create_record(
        self,
        entity: str,
        data: dict,
        tenant_id: int | None = None,
    ) -> dict:
        """Create and validate a record; relations use ids and password is hashed."""
        self._require_admin()
        model = self._entity(entity)
        data = dict(data or {})
        password = data.pop('password', None) if model is self._user_model() else None
        obj, many_to_many = self._apply_data(model, data)
        if self._is_tenant_aware(model):
            effective_tenant_id = tenant_id or getattr(
                obj, f'{TENANT_FK}_id', None
            )
            if not effective_tenant_id:
                raise ValueError(
                    f"A entidade '{entity}' é tenant-aware: informe "
                    "'tenant_id'."
                )
            setattr(obj, f'{TENANT_FK}_id', effective_tenant_id)
        if password is not None:
            obj.set_password(password)
        try:
            with transaction.atomic():
                obj.full_clean()
                obj.save()
                self._apply_many_to_many(obj, many_to_many)
                self._record_audit(
                    'mcp_create_record',
                    obj,
                    {'entity': entity},
                )
        except DjangoValidationError as exc:
            raise ValueError(self._format_validation_error(exc)) from exc
        return self._serialize(obj)

    def update_record(self, entity: str, id: int, data: dict) -> dict:
        """Partially update and validate a record; user password is re-hashed."""
        self._require_admin()
        model = self._entity(entity)
        obj = self._get_object(model, id)
        data = dict(data or {})
        password = data.pop('password', None) if model is self._user_model() else None
        if model is self._user_model() and obj.pk == self._user().pk:
            next_active = data.get('is_active', obj.is_active)
            next_staff = data.get('is_staff', obj.is_staff)
            next_superuser = data.get('is_superuser', obj.is_superuser)
            if not next_active or not (next_staff or next_superuser):
                raise ValueError(
                    'Não é possível remover o próprio acesso administrativo.'
                )
        obj, many_to_many = self._apply_data(model, data, instance=obj)
        if password:
            obj.set_password(password)
        try:
            with transaction.atomic():
                obj.full_clean()
                obj.save()
                self._apply_many_to_many(obj, many_to_many)
                self._record_audit(
                    'mcp_update_record',
                    obj,
                    {'entity': entity, 'updated_fields': sorted(data)},
                )
        except DjangoValidationError as exc:
            raise ValueError(self._format_validation_error(exc)) from exc
        return self._serialize(obj)

    def delete_record(self, entity: str, id: int) -> dict:
        """Delete a record and report cascades; self-deletion is forbidden."""
        self._require_admin()
        model = self._entity(entity)
        obj = self._get_object(model, id)
        if model is self._user_model() and obj.pk == self._user().pk:
            raise ValueError(
                'Não é possível excluir o próprio usuário autenticado.'
            )
        self._validate_delete(model, obj)
        label = str(obj)
        object_id = obj.pk
        try:
            with transaction.atomic():
                self._record_audit(
                    'mcp_delete_record',
                    obj,
                    {'entity': entity, 'deleted_label': label},
                )
                affected, per_model = obj.delete()
        except (ProtectedError, RestrictedError) as exc:
            raise ValueError(
                'Exclusão bloqueada por registros relacionados protegidos.'
            ) from exc
        except DjangoValidationError as exc:
            raise ValueError(self._format_validation_error(exc)) from exc
        return {
            'entity': entity,
            'id': object_id,
            'deleted': label,
            'affected_objects': affected,
            'detail': self._to_jsonable(per_model),
        }

    # -- Domain metrics --------------------------------------------------
    def general_metrics(self, tenant_id: int | None = None) -> dict:
        """Return the main operational and financial totals."""
        self._require_admin()
        customer_qs = self._scoped('customers.Customer', tenant_id)
        product_qs = self._scoped('catalog.Product', tenant_id)
        rental_qs = self._scoped('rentals.Rental', tenant_id)
        receivable_qs = self._scoped('billing.Receivable', tenant_id)
        payment_qs = self._scoped('billing.Payment', tenant_id).filter(
            is_reversal=False,
            reversed_by__isnull=True,
        )
        open_receivables = receivable_qs.filter(balance__gt=0)
        return {
            'customers_total': customer_qs.count(),
            'active_customers': customer_qs.filter(is_active=True).count(),
            'products_total': product_qs.count(),
            'active_products': product_qs.filter(is_active=True).count(),
            'rentals_total': rental_qs.count(),
            'open_receivables': open_receivables.count(),
            'outstanding_value': self._sum(open_receivables, 'balance'),
            'payments_total': payment_qs.count(),
            'payments_value': self._sum(payment_qs, 'amount'),
        }

    def rental_metrics(
        self,
        tenant_id: int | None = None,
        upcoming_days: int = 7,
    ) -> dict:
        """Return rental status, pickup, overdue and conversion indicators."""
        self._require_admin()
        from rentals.models import Rental

        today = timezone.localdate()
        days = self._clamp(upcoming_days, default=7, maximum=90)
        queryset = self._scoped('rentals.Rental', tenant_id)
        by_status = {
            row['status']: row['total']
            for row in queryset.values('status').annotate(total=Count('pk'))
        }
        total = queryset.count()
        cancelled = by_status.get(Rental.Status.CANCELLED, 0)
        eligible = total - cancelled
        returned = by_status.get(Rental.Status.RETURNED, 0)
        return {
            'total': total,
            'by_status': by_status,
            'pickups_today': queryset.filter(
                status=Rental.Status.PENDING,
                pickup_date=today,
            ).count(),
            'upcoming_pickups': queryset.filter(
                status=Rental.Status.PENDING,
                pickup_date__gt=today,
                pickup_date__lte=today + timedelta(days=days),
            ).count(),
            'overdue_returns': queryset.filter(
                status=Rental.Status.PICKED_UP,
                return_date__lt=today,
            ).count(),
            'return_conversion_percent': self._percentage(returned, eligible),
            'cancellation_rate_percent': self._percentage(cancelled, total),
            'contracted_value': self._sum(queryset, 'total_value'),
        }

    def billing_metrics(
        self,
        tenant_id: int | None = None,
        due_within_days: int = 7,
    ) -> dict:
        """Return receivable, paid, overdue and due-soon financial indicators."""
        self._require_admin()
        today = timezone.localdate()
        days = self._clamp(due_within_days, default=7, maximum=90)
        receivables = self._scoped('billing.Receivable', tenant_id)
        open_qs = receivables.filter(balance__gt=0)
        overdue_qs = open_qs.filter(due_date__lt=today)
        due_soon_qs = open_qs.filter(
            due_date__gte=today,
            due_date__lte=today + timedelta(days=days),
        )
        active_payments = self._scoped('billing.Payment', tenant_id).filter(
            is_reversal=False,
            reversed_by__isnull=True,
        )
        reversals = self._scoped('billing.Payment', tenant_id).filter(
            is_reversal=True
        )
        billed_value = self._sum(receivables, 'amount')
        paid_value = self._sum(receivables, 'paid_amount')
        return {
            'receivables_total': receivables.count(),
            'open_receivables': open_qs.count(),
            'open_value': self._sum(open_qs, 'balance'),
            'overdue_receivables': overdue_qs.count(),
            'overdue_value': self._sum(overdue_qs, 'balance'),
            'due_soon_receivables': due_soon_qs.count(),
            'due_soon_value': self._sum(due_soon_qs, 'balance'),
            'payments_total': active_payments.count(),
            'payments_value': self._sum(active_payments, 'amount'),
            'reversals_total': reversals.count(),
            'reversals_value': self._sum(reversals, 'amount'),
            'collection_rate_percent': self._percentage(
                paid_value, billed_value
            ),
        }

    def catalog_metrics(self, tenant_id: int | None = None) -> dict:
        """Return category, inventory value and product utilization metrics."""
        self._require_admin()
        from rentals.models import Rental

        categories = self._scoped('catalog.Category', tenant_id)
        products = self._scoped('catalog.Product', tenant_id)
        active_products = products.filter(is_active=True)
        occupied_product_ids = apps.get_model(
            'rentals.RentalItem'
        ).objects.filter(
            rental__status__in=[
                Rental.Status.PENDING,
                Rental.Status.PICKED_UP,
            ]
        ).values('product_id').distinct()
        occupied = active_products.filter(pk__in=occupied_product_ids).count()
        active_total = active_products.count()
        return {
            'categories_total': categories.count(),
            'products_total': products.count(),
            'active_products': active_total,
            'inactive_products': products.filter(is_active=False).count(),
            'placeholder_products': products.filter(is_placeholder=True).count(),
            'inventory_value': self._sum(active_products, 'value'),
            'occupied_products': occupied,
            'utilization_rate_percent': self._percentage(
                occupied, active_total
            ),
        }

    def notification_metrics(
        self,
        tenant_id: int | None = None,
        days: int = 30,
    ) -> dict:
        """Return WhatsApp message volume and delivery rates for a period."""
        self._require_admin()
        from notifications.models import CustomerMessage

        period = self._clamp(days, default=30, maximum=365)
        since = timezone.now() - timedelta(days=period)
        queryset = self._scoped(
            'notifications.CustomerMessage', tenant_id
        ).filter(created_at__gte=since)
        by_status = {
            row['status']: row['total']
            for row in queryset.values('status').annotate(total=Count('pk'))
        }
        by_kind = {
            row['kind']: row['total']
            for row in queryset.values('kind').annotate(total=Count('pk'))
        }
        sent = by_status.get(CustomerMessage.Status.SENT, 0)
        failed = by_status.get(CustomerMessage.Status.FAILED, 0)
        return {
            'period_days': period,
            'messages_total': queryset.count(),
            'by_status': by_status,
            'by_kind': by_kind,
            'delivery_rate_percent': self._percentage(sent, sent + failed),
        }

    def system_usage(self) -> dict:
        """Return adoption, administrator and recent activity indicators."""
        self._require_admin()
        user_model = self._user_model()
        since = timezone.now() - timedelta(days=30)
        return {
            'users_total': user_model.objects.count(),
            'active_users': user_model.objects.filter(is_active=True).count(),
            'admin_users': user_model.objects.filter(
                Q(is_staff=True) | Q(is_superuser=True)
            ).distinct().count(),
            'users_logged_in_last_30_days': user_model.objects.filter(
                last_login__gte=since
            ).count(),
            'rentals_created_last_30_days': apps.get_model(
                'rentals.Rental'
            ).objects.filter(created_at__gte=since).count(),
            'audit_events_last_30_days': self._audit_model().objects.filter(
                created_at__gte=since
            ).count(),
            'records_by_entity': {
                slug: apps.get_model(label)._default_manager.count()
                for slug, label in sorted(ENTITIES.items())
            },
        }
