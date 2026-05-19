from django import forms


class TestUploadForm(forms.Form):
    title = forms.CharField(
        label="Название теста",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "Например: Тест по Python"}),
    )
    description = forms.CharField(
        label="Описание",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    file = forms.FileField(label="TXT файл")

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        filename = uploaded_file.name.lower()

        if not filename.endswith((".txt", ".docx")):
            raise forms.ValidationError("Можно загрузить только .txt или .docx файл")

        if uploaded_file.size == 0:
            raise forms.ValidationError("Отправленный файл пуст.")

        return uploaded_file
