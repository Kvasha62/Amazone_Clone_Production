# ────────────────────────────────────────────────────────────────────────
# apps/core/admin_guards.py — PROD-004: Django Admin ↔ domain boundary.
#
# АРХИТЕКТУРНОЕ ПРАВИЛО (Issue #6 / PROD-004):
#   Django Admin — административный интерфейс, а НЕ альтернативный
#   доменный API. Переходы бизнес-состояний и бизнес-счётчики
#   (Order.status, Stock.quantity, Price.price, Shipment.status,
#   Coupon.times_used, CartItem.quantity/variant) меняются ТОЛЬКО через
#   авторитетный service-level путь. Admin не должен предоставлять
#   второй путь записи, обходящий инварианты, события, координацию,
#   валидацию или concurrency-контроль.
#
# ДВА СЛОЯ ЗАЩИТЫ (defense-in-depth, тот же приём, что ARCH-001 H2):
#   1. UI-слой — protected fields всегда попадают в readonly_fields,
#      поэтому сгенерированная ModelForm НЕ содержит для них input'ов.
#      Обычный Admin POST физически не может их связать.
#   2. Server-side слой — save_model() сравнивает in-memory объект со
#      строкой в БД и бросает PermissionDenied при расхождении, а
#      change-save пишет только явный update_fields-набор без
#      защищённых колонок. Это закрывает crafted POST, прямой вызов
#      save_model() и случайный будущий правкой readonly_fields.
#
# ПОЧЕМУ В apps/core:
#   core — foundation-контекст без доменных зависимостей (BaseModel).
#   Модуль импортирует только Django, поэтому любая app (orders,
#   inventory, pricing, shipping, discounts, cart) может его
#   использовать без нарушения направления зависимостей.
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   Шесть Admin-классов потеряют server-side слой защиты и перестанут
#   импортироваться (ImportError) → Django Admin не загрузится.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#django.contrib.admin.ModelAdmin.readonly_fields
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#django.contrib.admin.ModelAdmin.save_model
# ────────────────────────────────────────────────────────────────────────

from django.core.exceptions import PermissionDenied

# Маркер тикета в текстах ошибок — по нему легко найти правило в
# ARCHITECTURE.md / Issue #6.
ADMIN_GUARD_TICKET = 'PROD-004'


# ==============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==============================================================

def _protected_attnames(model, protected_fields):
    """Map {field_name: attname} for protected concrete fields.

    FK/OneToOne поля сравниваются по attname (``variant`` → ``variant_id``),
    потому что ``QuerySet.values()`` возвращает именно PK, а не объект.
    """
    return {
        name: model._meta.get_field(name).attname
        for name in protected_fields
    }


def _stored_values(model, pk):
    """Current DB values of all non-PK concrete fields as {attname: value}."""
    attnames = [
        field.attname
        for field in model._meta.concrete_fields
        if not field.primary_key
    ]
    return model.objects.filter(pk=pk).values(*attnames).first()


def _field_default(model, name):
    """Model-level default of a concrete field (``None`` when undefined)."""
    return model._meta.get_field(name).get_default()


def _auto_now_fields(model):
    """Names of ``auto_now`` concrete fields (must be in update_fields)."""
    return tuple(
        field.name
        for field in model._meta.concrete_fields
        if getattr(field, 'auto_now', False)
    )


def _denied(message):
    """PermissionDenied with the PROD-004 ticket marker."""
    return PermissionDenied(f'{message} ({ADMIN_GUARD_TICKET})')


class ProtectedFieldsAdminMixin:
    """Mixin для ModelAdmin: защищённые поля нельзя писать через Admin.

    КОНТРАКТ ДЛЯ НАСЛЕДНИКОВ:
      • ``protected_fields`` — кортеж имён бизнес-полей модели;
      • ``authoritative_path`` — человекочитаемый авторитетный путь
        (попадает в текст PermissionDenied, чтобы админ знал, куда идти).

    Пример::

        @admin.register(Coupon)
        class CouponAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
            protected_fields = ('times_used',)
            authoritative_path = 'DiscountService.register_usage()'

    📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#modeladmin-objects
    """

    # ── Контракт наследника ──
    protected_fields: tuple[str, ...] = ()
    authoritative_path: str = ''

    # ----------------------------------------------------------
    # СЛОЙ 1 — UI/форма: protected fields всегда read-only
    # ----------------------------------------------------------

    def get_readonly_fields(self, request, obj=None):
        """Append protected business fields to the declared readonly set.

        ``BaseModelAdmin.get_form()`` добавляет ``get_readonly_fields()``
        в ``exclude`` сгенерированной ModelForm, поэтому для защищённых
        полей не создаётся ни input, ни bound field — Admin POST не может
        их связать даже при явной подделке имени поля в теле запроса.
        """
        fields = super().get_readonly_fields(request, obj)
        return tuple(dict.fromkeys((*fields, *self.protected_fields)))

    # ----------------------------------------------------------
    # СЛОЙ 2 — server-side guard в save_model()
    # ----------------------------------------------------------

    def save_model(self, request, obj, form, change):
        """Refuse Admin writes of business-owned state, then save safely."""
        if change:
            self._guard_change_save(request, obj, form)
            return
        self._guard_add_save(request, obj, form)

    def _guard_change_save(self, request, obj, form):
        """Change path: no protected value may differ from the stored row.

        Дополнительно отказываемся от default-поведения ``obj.save()``
        (full-row UPDATE): пишем только поля фактической Admin-формы минус
        защищённые, поэтому защищённые колонки физически отсутствуют в
        SQL-запросе и не могут «перетереть» свежее значение, записанное
        сервисом между открытием формы и сохранением.
        """
        model = type(obj)
        if not self.protected_fields:
            super().save_model(request, obj, form, change=True)
            return

        if not obj.pk:
            raise _denied(
                'Сохранение без primary key через Admin change-save '
                f'запрещено для {model.__name__}: change path не должен '
                'выполнять full-row insert.'
            )

        previous = _stored_values(model, obj.pk)
        if previous is None:
            raise _denied(
                f'Сохранение устаревшего {model.__name__} через Admin '
                'запрещено: строка уже отсутствует в БД.'
            )

        attnames = _protected_attnames(model, self.protected_fields)
        changed = [
            name
            for name, attname in attnames.items()
            if getattr(obj, attname) != previous[attname]
        ]
        if changed:
            raise _denied(
                f'Изменение {model.__name__}.{", ".join(sorted(changed))} '
                'через Django Admin запрещено. Авторитетный путь: '
                f'{self.authoritative_path or "domain/application service"}.'
            )

        obj.save(update_fields=self._safe_update_fields(
            request, obj, form, previous,
        ))

    def _guard_add_save(self, request, obj, form):
        """Add path: protected values must start at their model defaults.

        Создание строки с «заранее выставленным» бизнес-значением —
        тот же обход сервиса, что и изменение существующего: например
        ``Stock(quantity=999)`` или ``Price(price=1.00)`` через Admin
        публикуют состояние в обход InventoryService / PricingService.
        """
        model = type(obj)
        offending = [
            name
            for name in self.protected_fields
            if getattr(obj, _protected_attnames(model, (name,))[name])
            != _field_default(model, name)
        ]
        if offending:
            raise _denied(
                f'Задание {model.__name__}.{", ".join(sorted(offending))} '
                'через Django Admin запрещено. Авторитетный путь: '
                f'{self.authoritative_path or "domain/application service"}.'
            )

        super().save_model(request, obj, form, change=False)

    # ----------------------------------------------------------
    # Вспомогательные
    # ----------------------------------------------------------

    def _safe_update_fields(self, request, obj, form, previous):
        """UPDATE field-set: form fields minus protected, plus auto_now."""
        model = type(obj)
        protected_attnames = set(
            _protected_attnames(model, self.protected_fields).values()
        )
        protected_names = set(self.protected_fields)
        form_field_names = self._form_field_names(request, obj, form)
        changed_form_fields = (
            set(form.changed_data) if form is not None else None
        )

        update_fields = []
        for field in model._meta.concrete_fields:
            if field.primary_key:
                continue
            if field.name in protected_names or field.attname in protected_attnames:
                continue
            if field.name not in form_field_names:
                continue

            if changed_form_fields is not None:
                field_changed = field.name in changed_form_fields
            else:
                field_changed = (
                    getattr(obj, field.attname) != previous[field.attname]
                )
            if field_changed:
                update_fields.append(field.name)

        # auto_now не обновится, если поля нет в update_fields.
        for name in _auto_now_fields(model):
            if name not in update_fields:
                update_fields.append(name)

        return tuple(update_fields)

    def _form_field_names(self, request, obj, form):
        """Field names owned by the real Admin form (bound or unbound)."""
        if form is not None:
            return frozenset(form.fields)
        form_class = self.get_form(request, obj=obj, change=True)
        return frozenset(form_class.base_fields)


class ProtectedFieldsInlineMixin:
    """Mixin для InlineModelAdmin: те же правила, что и для ModelAdmin.

    Inline — это ВТОРОЙ Admin-путь записи тех же бизнес-полей (POST на
    страницу родителя), поэтому защита только standalone-страницы
    оставляет дыру. Inline не имеет ``save_model``, поэтому server-side
    слой вызывается родителем через ``guard_inline_formsets()``
    (публичный хук ``ModelAdmin.save_formset``).
    """

    protected_fields: tuple[str, ...] = ()
    authoritative_path: str = ''

    def get_readonly_fields(self, request, obj=None):
        """Append protected business fields to the inline readonly set."""
        fields = super().get_readonly_fields(request, obj)
        return tuple(dict.fromkeys((*fields, *self.protected_fields)))

    def assert_formset_protected(self, formset):
        """Raise PermissionDenied if a bound formset would write them.

        Вызывается ДО ``formset.save()``: к этому моменту валидация уже
        прошла и ``form.instance`` содержит связанные данные формы, так
        что сравнение со строкой в БД честно отражает будущий UPDATE.
        """
        model = formset.model
        if not self.protected_fields:
            return

        attnames = _protected_attnames(model, self.protected_fields)
        existing_pks = [
            form.instance.pk
            for form in formset.forms
            if form.instance.pk is not None
        ]
        stored = {
            row['pk']: row
            for row in model.objects.filter(pk__in=existing_pks)
            .values('pk', *attnames.values())
        }

        for form in formset.forms:
            obj = form.instance
            if obj.pk is None:
                offending = [
                    name
                    for name, attname in attnames.items()
                    if getattr(obj, attname) != _field_default(model, name)
                ]
                if offending:
                    raise _denied(
                        f'Создание {model.__name__} с '
                        f'{", ".join(sorted(offending))} через Django Admin '
                        'запрещено. Авторитетный путь: '
                        f'{self.authoritative_path or "domain/application service"}.'
                    )
                continue

            row = stored.get(obj.pk)
            if row is None:
                continue
            offending = [
                name
                for name, attname in attnames.items()
                if getattr(obj, attname) != row[attname]
            ]
            if offending:
                raise _denied(
                    f'Изменение {model.__name__}.{", ".join(sorted(offending))} '
                    'через Django Admin (inline) запрещено. Авторитетный путь: '
                    f'{self.authoritative_path or "domain/application service"}.'
                )


def guard_inline_formsets(model_admin, request, formset):
    """Server-side guard для inline-формсетов на странице родителя.

    Используется из ``ModelAdmin.save_formset()``: находит inline-класс,
    соответствующий модели формсета, и применяет его protected-field
    правила. Formset'ы без ``protected_fields`` пропускаются — обычное
    поведение Admin не меняется.

    📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#django.contrib.admin.ModelAdmin.save_formset
    """
    inline = None
    for candidate in model_admin.get_inline_instances(
        request, obj=formset.instance,
    ):
        if candidate.model is formset.model:
            inline = candidate
            break

    if inline is None:
        return
    if not isinstance(inline, ProtectedFieldsInlineMixin):
        return

    inline.assert_formset_protected(formset)
