from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from billing.models import Payment
from catalog.models import Product
from core.ui import (
    BRMoneyField,
    DATE_INPUT_ATTRS,
    DATE_INPUT_FORMATS,
    INPUT_CLASS,
    configure_br_decimal_field,
)

from customers.models import Customer
from .models import Rental, RentalItem

# What the printed contract holds: 15 item lines, and one mandatory down payment
# plus at most 8 future installments. Confirmed with the shop on 2026-08-02.
#
# The 15 is measured, not guessed. Two copies share one A4 sheet of 285mm; the
# pair takes 278.0mm with 14 items, 281.2mm with 15, and exactly 285.0mm with 16
# — no margin left, so a longer description would push a copy onto a second
# sheet. 15 also matches the legacy ceiling: the imported data tops out at 15.
MAX_ITEMS_PER_RENTAL = 15
MAX_FUTURE_INSTALLMENTS = 8

MAX_PROOF_PHOTO_UPLOAD_SIZE = 8 * 1024 * 1024
MAX_PROOF_PHOTO_PIXELS = 40_000_000
MAX_PROOF_PHOTO_EDGE = 1600
PROOF_PHOTO_JPEG_QUALITY = 84


def _style(form):
    for field_name, field in form.fields.items():
        if isinstance(field.widget, forms.Textarea):
            field.widget.attrs.setdefault('rows', 2)
        if isinstance(field, forms.DecimalField):
            configure_br_decimal_field(
                field,
                currency=field_name in {
                    'down_payment_amount', 'penalty_value', 'value', 'cash_discount_amount',
                },
                percent=field_name == 'cash_discount_percent',
            )
        css = field.widget.attrs.get('class', '')
        classes = css.split()
        if INPUT_CLASS not in classes:
            classes.append(INPUT_CLASS)
        field.widget.attrs['class'] = ' '.join(classes)


def process_proof_photo(uploaded_file):
    if uploaded_file.size > MAX_PROOF_PHOTO_UPLOAD_SIZE:
        raise ValidationError('Envie uma imagem de até 8 MB.')

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            if image.width * image.height > MAX_PROOF_PHOTO_PIXELS:
                raise ValidationError(
                    'A imagem possui resolução excessiva. Envie uma foto menor.'
                )
            image.load()
            image = ImageOps.exif_transpose(image)
            if image.mode in ('RGBA', 'LA') or (
                image.mode == 'P' and 'transparency' in image.info
            ):
                alpha_image = image.convert('RGBA')
                background = Image.new('RGB', alpha_image.size, (255, 255, 255))
                background.paste(alpha_image, mask=alpha_image.getchannel('A'))
                image = background
            elif image.mode != 'RGB':
                image = image.convert('RGB')

            image.thumbnail(
                (MAX_PROOF_PHOTO_EDGE, MAX_PROOF_PHOTO_EDGE),
                Image.Resampling.LANCZOS,
            )
            width, height = image.size
            output = BytesIO()
            image.save(
                output,
                format='JPEG',
                quality=PROOF_PHOTO_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError('Envie uma imagem válida em JPG, PNG ou WebP.') from exc
    finally:
        uploaded_file.seek(0)

    stem = Path(uploaded_file.name or 'foto').stem[:120] or 'foto'
    filename = f'{stem}.jpg'
    data = output.getvalue()
    return {
        'file': ContentFile(data, name=filename),
        'content_type': 'image/jpeg',
        'filename': filename,
        'size': len(data),
        'width': width,
        'height': height,
    }


class RentalForm(forms.ModelForm):
    """Rental header form. Number, total and status are managed by the view."""

    # Extra: installment generation (R7.05)
    installment_count = forms.IntegerField(
        label='Número de parcelas futuras', min_value=1, max_value=MAX_FUTURE_INSTALLMENTS, initial=1,
        required=False,
        help_text='O saldo após a entrada será dividido igualmente entre elas.',
    )
    first_due_date = forms.DateField(
        label='Data do próximo pagamento', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
        help_text=(
            'Deixe em branco para distribuir as parcelas mensalmente com a '
            'última vencendo na data de retirada. Se informado, as parcelas '
            'passam a vencer mensalmente a partir desta data.'
        ),
    )

    # Extra: down payment (R7.06)
    down_payment_amount = BRMoneyField(
        label='Valor da entrada', max_digits=10, decimal_places=2,
        min_value=0, required=False,
    )
    down_payment_method = forms.ChoiceField(
        label='Forma de recebimento da entrada',
        choices=[('', 'Selecione')] + list(Payment.Method.choices),
        required=False,
    )
    down_payment_date = forms.DateField(
        label='Data da entrada', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )

    class Meta:
        model = Rental
        fields = (
            'customer', 'pickup_date', 'return_date', 'penalty_value', 'wearer_name',
            'cash_discount', 'cash_discount_percent', 'cash_discount_amount', 'notes',
        )
        widgets = {
            'pickup_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
            'return_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields['penalty_value'].min_value = Decimal('0')
        self.fields['penalty_value'].validators.append(MinValueValidator(Decimal('0')))
        self.fields['cash_discount'].widget.attrs['class'] = (
            'rounded border-slate-300 text-rose-600 focus:ring-rose-500'
        )
        self.fields['cash_discount_percent'].required = False
        self.fields['cash_discount_percent'].min_value = Decimal('0')
        self.fields['cash_discount_percent'].validators.append(MinValueValidator(Decimal('0')))
        self.fields['cash_discount_percent'].validators.append(MaxValueValidator(Decimal('100')))
        self.fields['cash_discount_amount'].required = False
        self.fields['cash_discount_amount'].min_value = Decimal('0')
        self.fields['cash_discount_amount'].validators.append(MinValueValidator(Decimal('0')))
        for field_name in ('pickup_date', 'return_date'):
            self.fields[field_name].input_formats = DATE_INPUT_FORMATS
        # Hide select — JS search widget handles display; this avoids loading 18k+ options
        self.fields['customer'].widget.attrs['class'] = 'hidden'
        # ...but never let it render the HTML `required` attribute. The element is
        # display:none, and a browser cannot focus a hidden control to report a
        # constraint violation, so Chrome aborts the submit with no visible
        # message at all — the Save button just appears dead. The field stays
        # required server-side; its error renders next to the visible search box.
        self.fields['customer'].widget.use_required_attribute = lambda initial: False
        # Limit queryset to at most the relevant customer (huge performance gain)
        customer_id = None
        if self.instance and self.instance.pk:
            customer_id = getattr(self.instance, 'customer_id', None)
        if customer_id is None and self.data.get('customer'):
            try:
                customer_id = int(self.data['customer'])
            except (ValueError, TypeError):
                pass
        if customer_id:
            selected_customer_is_current = (
                self.instance
                and self.instance.pk
                and self.instance.customer_id == customer_id
            )
            customers = Customer.objects.filter(pk=customer_id)
            if not selected_customer_is_current:
                customers = customers.filter(is_active=True)
            self.fields['customer'].queryset = customers.only('pk', 'name')
        else:
            self.fields['customer'].queryset = Customer.objects.none()

    def clean(self):
        cleaned = super().clean()
        pickup = cleaned.get('pickup_date')
        return_d = cleaned.get('return_date')
        if pickup and return_d and return_d <= pickup:
            self.add_error('return_date', 'Data de retorno deve ser posterior à data de retirada.')
        discount_percent = cleaned.get('cash_discount_percent')
        discount_amount = cleaned.get('cash_discount_amount')
        if discount_percent is not None and discount_amount is not None:
            self.add_error(
                'cash_discount_amount',
                'Informe o desconto em porcentagem OU em reais, não os dois.',
            )
        if (discount_percent is not None or discount_amount is not None) and not cleaned.get('cash_discount'):
            self.add_error(
                'cash_discount',
                'Marque "desconto à vista" para aplicar o percentual ou valor informado.',
            )
        dp_amount = cleaned.get('down_payment_amount')
        dp_method = cleaned.get('down_payment_method')
        dp_date = cleaned.get('down_payment_date')
        first_due_date = cleaned.get('first_due_date')
        if dp_amount and dp_amount > 0:
            if not dp_method:
                self.add_error('down_payment_method', 'Informe a forma de recebimento da entrada.')
            if not dp_date:
                self.add_error('down_payment_date', 'Informe a data da entrada.')
            elif dp_date > timezone.localdate():
                self.add_error(
                    'down_payment_date',
                    'A data da entrada não pode estar no futuro.',
                )
            if dp_date and first_due_date and first_due_date < dp_date:
                self.add_error(
                    'first_due_date',
                    'O próximo vencimento não pode ser anterior à data da entrada.',
                )
        return cleaned


class RentalItemForm(forms.ModelForm):
    proof_photo_upload = forms.ImageField(
        label='Foto com a peça',
        required=False,
        help_text='JPG, PNG ou WebP até 8 MB. A imagem será ajustada automaticamente.',
        widget=forms.ClearableFileInput(
            attrs={'accept': 'image/jpeg,image/png,image/webp'}
        ),
    )

    class Meta:
        model = RentalItem
        fields = ('product', 'description', 'value')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.processed_proof_photo = None
        self.clear_proof_photo = False
        _style(self)
        self.fields['value'].min_value = Decimal('0')
        self.fields['value'].validators.append(MinValueValidator(Decimal('0')))
        # A new line must not silently become a R$ 0,00 item.  Leaving this
        # blank lets the picker copy the product's suggested price, while
        # non-JavaScript users still receive the normal required-field error.
        if not self.is_bound and not self.instance.pk:
            self.initial['value'] = ''
        # Hidden select populated by the AJAX product search; keep only the selected
        # product in the queryset to avoid rendering thousands of options per row.
        self.fields['product'].widget.attrs['class'] = 'hidden'
        self.fields['description'].widget = forms.HiddenInput()
        product_id = None
        if self.is_bound:
            value = self.data.get(self.add_prefix('product'))
            try:
                product_id = int(value)
            except (TypeError, ValueError):
                pass
        if product_id is None and self.instance and self.instance.pk:
            product_id = getattr(self.instance, 'product_id', None)
        if product_id:
            selected_product_is_current = (
                self.instance
                and self.instance.pk
                and self.instance.product_id == product_id
            )
            products = Product.objects.filter(pk=product_id)
            if not selected_product_is_current:
                products = products.filter(is_active=True)
            self.fields['product'].queryset = products.select_related('category')
        else:
            self.fields['product'].queryset = Product.objects.none()

    @property
    def typed_product_search(self):
        """Whatever was left in this row's product search box.

        That box is a JS combobox, not a form field, so the text only survives
        a re-render if it is echoed back explicitly.
        """
        if not self.is_bound:
            return ''
        return (self.data.get(f'{self.add_prefix("product")}_search') or '').strip()

    def has_user_input(self):
        """Whether anything was actually entered in this new row.

        Broader than ``has_changed`` on purpose: this decides whether the row is
        worth *showing* again, while ``has_changed`` decides whether it is worth
        *validating*. A row someone typed a price or a product name into is not
        the blank slot, even when it is too incomplete to save.
        """
        for field_name in ('product', 'value', 'description'):
            if (self.data.get(self.add_prefix(field_name)) or '').strip():
                return True
        if self.files.get(self.add_prefix('proof_photo_upload')):
            return True
        return bool(self.typed_product_search)

    def has_changed(self):
        # An unsaved row with no product chosen is an empty row and must be
        # ignored — never saved, never validated. Without this, the model's
        # ``value`` default (0) makes a blank form look "changed" (submitted
        # '' != initial 0), so leftover empty items pile up and block saving.
        # Kept deliberately narrow: widening it here would turn a half-typed row
        # into a hard validation error that blocks the whole save. Whether the
        # row is re-rendered is decided by ``has_user_input`` instead.
        if not (self.instance and self.instance.pk):
            product = (self.data.get(self.add_prefix('product')) or '').strip()
            if not product:
                return False
        return super().has_changed()

    def clean_proof_photo_upload(self):
        uploaded_file = self.cleaned_data.get('proof_photo_upload')
        if uploaded_file is False:
            self.clear_proof_photo = True
            return False
        if not uploaded_file:
            return uploaded_file
        self.processed_proof_photo = process_proof_photo(uploaded_file)
        return uploaded_file

    def save(self, commit=True):
        instance = super().save(commit=False)
        if getattr(self, 'clear_proof_photo', False):
            if instance.proof_photo:
                instance.proof_photo.delete(save=False)
            instance.proof_photo = ''
            instance.proof_photo_content_type = ''
            instance.proof_photo_filename = ''
            instance.proof_photo_size = 0
            instance.proof_photo_width = 0
            instance.proof_photo_height = 0
        elif self.processed_proof_photo:
            if instance.proof_photo:
                instance.proof_photo.delete(save=False)
            instance.proof_photo = self.processed_proof_photo['file']
            instance.proof_photo_content_type = self.processed_proof_photo['content_type']
            instance.proof_photo_filename = self.processed_proof_photo['filename']
            instance.proof_photo_size = self.processed_proof_photo['size']
            instance.proof_photo_width = self.processed_proof_photo['width']
            instance.proof_photo_height = self.processed_proof_photo['height']
        if commit:
            instance.save()
        return instance


class BaseRentalItemFormSet(forms.BaseInlineFormSet):
    """Shared rules for rental item formsets (create + edit).

    - Only loads items that actually point to a product, so legacy/orphaned
      rows without a resolvable product never render as ghost "empty" items.
    - Blocks the same product being booked twice in one rental (one physical
      piece = one line), surfacing the error on the offending item.
    """

    default_error_messages = {
        'too_few_forms': 'Inclua ao menos uma peça na locação.',
    }

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(product__isnull=False).select_related('product__category')

    @property
    def visible_forms(self):
        """The rows the items grid should render.

        On an unbound formset that is simply every form. On a re-render after a
        failed save it keeps saved rows plus anything the user typed into, so a
        half-filled line survives with its input instead of silently vanishing
        while they are still looking for what went wrong. Blank slots are
        dropped so they never pile up, and the list is never empty — an items
        grid with no row at all leaves nowhere to type.
        """
        if not self.is_bound:
            return self.forms
        kept = [
            form for form in self.forms
            if form.instance.pk
            or form.has_changed()
            or form.errors
            or form.has_user_input()
        ]
        return kept or self.forms[:1]

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        # The printed contract fits MAX_ITEMS_PER_RENTAL pieces; beyond that the
        # list runs off the page silently. Imported legacy rentals already exceed
        # it (88 of them carry 15 items), so the cap only blocks *growth* — an
        # over-limit rental stays editable, and swapping a piece for another keeps
        # the same count. Same spirit as the duplicate rule below.
        kept = [
            form for form in self.forms
            if getattr(form, 'cleaned_data', None)
            and not form.cleaned_data.get('DELETE')
            and form.cleaned_data.get('product')
        ]
        if len(kept) > MAX_ITEMS_PER_RENTAL and len(kept) > self.initial_form_count():
            raise forms.ValidationError(
                f'A locação pode ter no máximo {MAX_ITEMS_PER_RENTAL} peças — '
                'é o que cabe no contrato impresso.',
                code='too_many_items',
            )

        seen = set()
        for form in self.forms:
            if not getattr(form, 'cleaned_data', None):
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            product = form.cleaned_data.get('product')
            if not product:
                continue
            if product.pk in seen:
                # Only block a *newly added* duplicate. Pre-existing (saved)
                # duplicate rows in legacy rentals stay editable for backward
                # compatibility — saved rows are iterated before extra rows.
                if not form.instance.pk:
                    form.add_error('product', 'Esta peça já foi adicionada a esta locação.')
            else:
                seen.add(product.pk)


RentalItemFormSet = forms.inlineformset_factory(
    Rental,
    RentalItem,
    form=RentalItemForm,
    formset=BaseRentalItemFormSet,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


RentalItemEditFormSet = forms.inlineformset_factory(
    Rental,
    RentalItem,
    form=RentalItemForm,
    formset=BaseRentalItemFormSet,
    extra=0,
    can_delete=True,
)


class RentalCancelForm(forms.Form):
    reason = forms.CharField(
        label='Motivo do cancelamento',
        widget=forms.Textarea(attrs={'rows': 3}),
        min_length=5,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLASS
