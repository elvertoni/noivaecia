from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    MinimumLengthValidator,
    NumericPasswordValidator,
    UserAttributeSimilarityValidator,
)


class AppUserAttributeSimilarityValidator(UserAttributeSimilarityValidator):
    def get_help_text(self):
        return 'Evite usar nome, e-mail ou dados pessoais.'

    def get_error_message(self):
        return 'Use uma senha diferente do seu nome ou e-mail.'


class AppMinimumLengthValidator(MinimumLengthValidator):
    def get_help_text(self):
        return f'Use pelo menos {self.min_length} caracteres.'

    def get_error_message(self):
        return f'Use pelo menos {self.min_length} caracteres.'


class AppCommonPasswordValidator(CommonPasswordValidator):
    def get_help_text(self):
        return 'Evite senhas comuns.'

    def get_error_message(self):
        return 'Escolha uma senha menos comum.'


class AppNumericPasswordValidator(NumericPasswordValidator):
    def get_help_text(self):
        return 'Não use apenas números.'

    def get_error_message(self):
        return 'Inclua letras ou símbolos; não use apenas números.'
