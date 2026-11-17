from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label


class MyApp(App):
    def build(self):
        self.count = 0

        # Вертикальный контейнер с отступами
        layout = BoxLayout(orientation="vertical", padding=40, spacing=20)

        self.label = Label(
            text="Нажатий: 0",
            font_size="32sp",
        )
        btn = Button(
            text="Нажми на меня!",
            font_size="24sp",
            size_hint=(1, 0.4),
        )
        btn.bind(on_press=self.on_button_press)

        layout.add_widget(self.label)
        layout.add_widget(btn)
        return layout

    def on_button_press(self, instance):
        self.count += 1
        self.label.text = f"Нажатий: {self.count}"


if __name__ == "__main__":
    MyApp().run()
