from io import BytesIO
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from catalog.models import Product
from core.ui import DATE_INPUT_ATTRS, DATE_INPUT_FORMATS, INPUT_CLASS

from customers.models import Customer
from .models import Rental, RentalItem

MAX_PROOF_PHOTO_UPLOAD_SIZE = 8 * 1024 * 1024
MAX_PROOF_PHOTO_EDGE = 1600
PROOF_PHOTO_JPEG_QUALITY = 84


def _style(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.Textarea):
            field.widget.attrs.setdefault('rows', 3)
        css = field.widget.attrs.get('class', '')
        field.widget.attrs['class'] = (css + ' ' + INPUT_CLASS).strip()


def process_proof_photo(uploaded_file):
    if uploaded_file.size > MAX_PROOF_PHOTO_UPLOAD_SIZE:
        raise ValidationError('Envie uma imagem de até 8 MB.')

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
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
        label='Número de parcelas', min_value=1, max_value=9, initial=1,
        required=False,
        help_text='Deixe em branco para não gerar cobranças automaticamente.',
    )
    first_due_date = forms.DateField(
        label='1ª data de vencimento', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
        help_text='Padrão: data de retorno.',
    )

    # Extra: down payment (R7.06)
    down_payment_amount = forms.DecimalField(
        label='Valor da entrada', max_digits=10, decimal_places=2,
        min_value=0, required=False,
    )
    down_payment_method = forms.ChoiceField(
        label='Forma de recebimento da entrada',
        choices=[('', '—')] + [
            ('cash', 'Dinheiro'), ('pix', 'PIX'), ('card_debit', 'Cartão débito'),
            ('card_credit', 'Cartão crédito'), ('bank_transfer', 'Transferência'),
            ('check', 'Cheque'),
        ],
        required=False,
    )
    down_payment_date = forms.DateField(
        label='Data da entrada', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )

    class Meta:
        model = Rental
        fields = ('customer', 'use_for', 'pickup_date', 'return_date', 'penalty_value', 'notes')
        widgets = {
            'pickup_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
            'return_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        for field_name in ('pickup_date', 'return_date'):
            self.fields[field_name].input_formats = DATE_INPUT_FORMATS
        # Hide select — JS search widget handles display; this avoids loading 18k+ options
        self.fields['customer'].widget.attrs['class'] = 'hidden'
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
            self.fields['customer'].queryset = (
                Customer.objects.filter(pk=customer_id).only('pk', 'name')
            )
        else:
            self.fields['customer'].queryset = Customer.objects.none()

    def clean(self):
        cleaned = super().clean()
        pickup = cleaned.get('pickup_date')
        return_d = cleaned.get('return_date')
        if pickup and return_d and return_d <= pickup:
            self.add_error('return_date', 'Data de retorno deve ser posterior à data de retirada.')
        dp_amount = cleaned.get('down_payment_amount')
        dp_method = cleaned.get('down_payment_method')
        dp_date = cleaned.get('down_payment_date')
        if dp_amount and dp_amount > 0:
            if not dp_method:
                self.add_error('down_payment_method', 'Informe a forma de recebimento da entrada.')
            if not dp_date:
                self.add_error('down_payment_date', 'Informe a data da entrada.')
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
        # Hidden select populated by the AJAX product search; keep only the selected
        # product in the queryset to avoid rendering thousands of options per row.
        self.fields['product'].widget.attrs['class'] = 'hidden'
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
            self.fields['product'].queryset = (
                Product.objects.filter(pk=product_id).select_related('category')
            )
        else:
            self.fields['product'].queryset = Product.objects.none()

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


RentalItemFormSet = forms.inlineformset_factory(
    Rental,
    RentalItem,
    form=RentalItemForm,
    extra=1,
    can_delete=True,
)


RentalItemEditFormSet = forms.inlineformset_factory(
    Rental,
    RentalItem,
    form=RentalItemForm,
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
