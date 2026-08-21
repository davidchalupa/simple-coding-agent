import sys
from datetime import date
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHBoxLayout, QMessageBox, QCheckBox
)

class CalculatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Investment Calculator")
        # Make window larger
        self.resize(1075, 600)
        self._init_ui()

    def _init_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout()

        # Input fields (2 + 2 text boxes)
        input_layout1 = QHBoxLayout()
        label1 = QLabel("Current value (total):")
        self.input_current_value_total = QLineEdit()
        input_layout1.addWidget(label1)
        input_layout1.addWidget(self.input_current_value_total)

        input_layout3 = QHBoxLayout()
        label2 = QLabel("Current value (investment):")
        self.input_current_value_invest = QLineEdit()
        label5 = QLabel("Initial value (investment):")
        self.input_initial_value_invest = QLineEdit()
        input_layout3.addWidget(label2)
        input_layout3.addWidget(self.input_current_value_invest)
        input_layout3.addWidget(label5)
        input_layout3.addWidget(self.input_initial_value_invest)

        input_layout2 = QHBoxLayout()
        label3 = QLabel("Monthly income (net):")
        self.input_income_monthly = QLineEdit()
        label4 = QLabel("Monthly expenses:")
        self.input_expenses_monthly = QLineEdit()
        input_layout2.addWidget(label3)
        input_layout2.addWidget(self.input_income_monthly)
        input_layout2.addWidget(label4)
        input_layout2.addWidget(self.input_expenses_monthly)

        input_layout4 = QHBoxLayout()
        self.checkbox_apply_taxes = QCheckBox("Apply taxes")
        label6 = QLabel("Tax rate (%):")
        self.input_tax_rate = QLineEdit()
        input_layout4.addWidget(self.checkbox_apply_taxes)
        input_layout4.addWidget(label6)
        input_layout4.addWidget(self.input_tax_rate)

        input_layout5 = QHBoxLayout()
        label7 = QLabel("Income growth rate (%):")
        self.input_income_growth_rate = QLineEdit()
        label8 = QLabel("Inflation (%):")
        self.input_inflation = QLineEdit()
        input_layout5.addWidget(label7)
        input_layout5.addWidget(self.input_income_growth_rate)
        input_layout5.addWidget(label8)
        input_layout5.addWidget(self.input_inflation)

        self.calc_button = QPushButton("Calculate")
        self.calc_button.clicked.connect(self.on_calculate)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(8)
        header_labels = [
            "Year",
            "Income",
            "Expenses",
        ]
        for roi in [1.06, 1.07, 1.08, 1.09, 1.1]:
            header_labels.append(f"ROI: {roi}\n5Y ROI: {pow(roi, 5):.3f}")
        self.result_table.setHorizontalHeaderLabels(header_labels)
        # Set the number of rows to 30
        row_count = 30
        self.result_table.setRowCount(row_count)

        # Make rows thinner
        for row in range(row_count):
            self.result_table.setRowHeight(row, 20)  # adjust height as needed

        main_layout.addLayout(input_layout1)
        main_layout.addLayout(input_layout3)
        main_layout.addLayout(input_layout2)
        main_layout.addLayout(input_layout4)
        main_layout.addLayout(input_layout5)
        main_layout.addWidget(self.calc_button)
        main_layout.addWidget(self.result_table)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def on_calculate(self):
        # ensure all inputs are filled
        texts = [
            self.input_current_value_total.text(),
            self.input_current_value_invest.text(),
            self.input_initial_value_invest.text(),
            self.input_income_monthly.text(),
            self.input_expenses_monthly.text(),
        ]
        apply_taxes = self.checkbox_apply_taxes.isChecked()
        if apply_taxes:
            texts.append(self.input_tax_rate.text())
        if any(not txt.strip() for txt in texts):
            QMessageBox.warning(
                self,
                "Input Error",
                "Please fill in all input boxes before calculating."
            )
            return

        if apply_taxes:
            tax_factor = 1.0 - float(self.input_tax_rate.text()) / 100.0
        income_growth_factor = 1.0
        if self.input_income_growth_rate.text() != "":
            income_growth_factor += float(self.input_income_growth_rate.text()) / 100.0
        inflation_factor = 1.0
        if self.input_inflation.text() != "":
            inflation_factor += float(self.input_inflation.text()) / 100.0
        row_count = self.result_table.rowCount()
        for row in range(row_count):
            current_year = date.today().year
            self.result_table.setItem(row, 0, QTableWidgetItem(str(current_year + row)))
        rois = [1.06, 1.07, 1.08, 1.09, 1.1]
        income_yearly_initial = float(self.input_income_monthly.text()) * 12
        expenses_yearly_initial = float(self.input_expenses_monthly.text()) * 12
        init_value_invest = float(self.input_initial_value_invest.text())
        for col in range(1, 6):
            non_taxable_value_invest = init_value_invest
            current_value_invest = float(self.input_current_value_invest.text())
            current_value_other = float(self.input_current_value_total.text()) - current_value_invest
            income_yearly = income_yearly_initial
            expenses_yearly = expenses_yearly_initial
            for row in range(row_count):
                self.result_table.setItem(row, 1, QTableWidgetItem(f"{(income_yearly):.2f}"))
                self.result_table.setItem(row, 2, QTableWidgetItem(f"{(expenses_yearly):.2f}"))
                if apply_taxes:
                    current_value = (non_taxable_value_invest +
                                     (current_value_invest - non_taxable_value_invest) * tax_factor +
                                     current_value_other)
                else:
                    current_value = current_value_invest + current_value_other
                self.result_table.setItem(
                    row, col + 2,
                    QTableWidgetItem(f"{(current_value):.2f}")
                )
                current_value_invest *= rois[col - 1]
                current_value_invest += income_yearly - expenses_yearly
                non_taxable_value_invest += income_yearly - expenses_yearly
                income_yearly *= income_growth_factor
                expenses_yearly *= inflation_factor

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CalculatorApp()
    window.show()
    sys.exit(app.exec_())
