# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
import unittest

import frappe
from frappe.utils import random_string

from erpnext.manufacturing.doctype.job_card.job_card import OperationMismatchError, OverlapError
from erpnext.manufacturing.doctype.job_card.job_card import (
	make_stock_entry as make_stock_entry_from_jc,
)
from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record
from erpnext.manufacturing.doctype.workstation.test_workstation import make_workstation
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry


class TestJobCard(unittest.TestCase):

	def setUp(self):
		transfer_material_against, source_warehouse = None, None

		tests_that_skip_setup = (
			"test_job_card_material_transfer_correctness",
		)
		tests_that_transfer_against_jc = (
			"test_job_card_multiple_materials_transfer",
			"test_job_card_excess_material_transfer",
			"test_job_card_partial_material_transfer"
		)

		if self._testMethodName in tests_that_skip_setup:
			return

		if self._testMethodName in tests_that_transfer_against_jc:
			transfer_material_against = "Job Card"
			source_warehouse = "Stores - _TC"

		self.work_order = make_wo_order_test_record(
			item="_Test FG Item 2",
			qty=2,
			transfer_material_against=transfer_material_against,
			source_warehouse=source_warehouse
		)

	def tearDown(self):
		frappe.db.rollback()

	def test_job_card(self):

		job_cards = frappe.get_all('Job Card',
			filters = {'work_order': self.work_order.name}, fields = ["operation_id", "name"])

		if job_cards:
			job_card = job_cards[0]
			frappe.db.set_value("Job Card", job_card.name, "operation_row_number", job_card.operation_id)

			doc = frappe.get_doc("Job Card", job_card.name)
			doc.operation_id = "Test Data"
			self.assertRaises(OperationMismatchError, doc.save)

		for d in job_cards:
			frappe.delete_doc("Job Card", d.name)

	def test_job_card_with_different_work_station(self):
		job_cards = frappe.get_all('Job Card',
			filters = {'work_order': self.work_order.name},
			fields = ["operation_id", "workstation", "name", "for_quantity"])

		job_card = job_cards[0]

		if job_card:
			workstation = frappe.db.get_value("Workstation",
				{"name": ("not in", [job_card.workstation])}, "name")

			if not workstation or job_card.workstation == workstation:
				workstation = make_workstation(workstation_name=random_string(5)).name

			doc = frappe.get_doc("Job Card", job_card.name)
			doc.workstation = workstation
			doc.append("time_logs", {
				"from_time": "2009-01-01 12:06:25",
				"to_time": "2009-01-01 12:37:25",
				"time_in_mins": "31.00002",
				"completed_qty": job_card.for_quantity
			})
			doc.submit()

			completed_qty = frappe.db.get_value("Work Order Operation", job_card.operation_id, "completed_qty")
			self.assertEqual(completed_qty, job_card.for_quantity)

			doc.cancel()

			for d in job_cards:
				frappe.delete_doc("Job Card", d.name)

	def test_job_card_overlap(self):
		wo2 = make_wo_order_test_record(item="_Test FG Item 2", qty=2)

		jc1_name = frappe.db.get_value("Job Card", {'work_order': self.work_order.name})
		jc2_name = frappe.db.get_value("Job Card", {'work_order': wo2.name})

		jc1 = frappe.get_doc("Job Card", jc1_name)
		jc2 = frappe.get_doc("Job Card", jc2_name)

		employee = "_T-Employee-00001" # from test records

		jc1.append("time_logs", {
			"from_time": "2021-01-01 00:00:00",
			"to_time": "2021-01-01 08:00:00",
			"completed_qty": 1,
			"employee": employee,
		})
		jc1.save()

		# add a new entry in same time slice
		jc2.append("time_logs", {
			"from_time": "2021-01-01 00:01:00",
			"to_time": "2021-01-01 06:00:00",
			"completed_qty": 1,
			"employee": employee,
		})
		self.assertRaises(OverlapError, jc2.save)

	def test_job_card_multiple_materials_transfer(self):
		"Test transferring RMs separately against Job Card with multiple RMs."
		make_stock_entry(
			item_code="_Test Item",
			target="Stores - _TC",
			qty=10,
			basic_rate=100
		)
		make_stock_entry(
			item_code="_Test Item Home Desktop Manufactured",
			target="Stores - _TC",
			qty=6,
			basic_rate=100
		)

		job_card_name = frappe.db.get_value("Job Card", {'work_order': self.work_order.name})
		job_card = frappe.get_doc("Job Card", job_card_name)

		transfer_entry_1 = make_stock_entry_from_jc(job_card_name)
		del transfer_entry_1.items[1] # transfer only 1 of 2 RMs
		transfer_entry_1.insert()
		transfer_entry_1.submit()

		job_card.reload()

		self.assertEqual(transfer_entry_1.fg_completed_qty, 2)
		self.assertEqual(job_card.transferred_qty, 2)

		# transfer second RM
		transfer_entry_2 = make_stock_entry_from_jc(job_card_name)
		del transfer_entry_2.items[0]
		transfer_entry_2.insert()
		transfer_entry_2.submit()

		# 'For Quantity' here will be 0 since
		# transfer was made for 2 fg qty in first transfer Stock Entry
		self.assertEqual(transfer_entry_2.fg_completed_qty, 0)

	def test_job_card_excess_material_transfer(self):
		"Test transferring more than required RM against Job Card."
		make_stock_entry(item_code="_Test Item", target="Stores - _TC",
			qty=25, basic_rate=100)
		make_stock_entry(item_code="_Test Item Home Desktop Manufactured",
			target="Stores - _TC", qty=15, basic_rate=100)

		job_card_name = frappe.db.get_value("Job Card", {'work_order': self.work_order.name})
		job_card = frappe.get_doc("Job Card", job_card_name)

		# fully transfer both RMs
		transfer_entry_1 = make_stock_entry_from_jc(job_card_name)
		transfer_entry_1.insert()
		transfer_entry_1.submit()

		# transfer extra qty of both RM due to previously damaged RM
		transfer_entry_2 = make_stock_entry_from_jc(job_card_name)
		# deliberately change 'For Quantity'
		transfer_entry_2.fg_completed_qty = 1
		transfer_entry_2.items[0].qty = 5
		transfer_entry_2.items[1].qty = 3
		transfer_entry_2.insert()
		transfer_entry_2.submit()

		job_card.reload()
		self.assertGreater(job_card.transferred_qty, job_card.for_quantity)

		# Check if 'For Quantity' is negative
		# as 'transferred_qty' > Qty to Manufacture
		transfer_entry_3 = make_stock_entry_from_jc(job_card_name)
		self.assertEqual(transfer_entry_3.fg_completed_qty, 0)

		job_card.append("time_logs", {
			"from_time": "2021-01-01 00:01:00",
			"to_time": "2021-01-01 06:00:00",
			"completed_qty": 2
		})
		job_card.save()
		job_card.submit()

		# JC is Completed with excess transfer
		self.assertEqual(job_card.status, "Completed")

	def test_job_card_partial_material_transfer(self):
		"Test partial material transfer against Job Card"

		make_stock_entry(item_code="_Test Item", target="Stores - _TC",
			qty=25, basic_rate=100)
		make_stock_entry(item_code="_Test Item Home Desktop Manufactured",
			target="Stores - _TC", qty=15, basic_rate=100)

		job_card_name = frappe.db.get_value("Job Card", {'work_order': self.work_order.name})
		job_card = frappe.get_doc("Job Card", job_card_name)

		# partially transfer
		transfer_entry = make_stock_entry_from_jc(job_card_name)
		transfer_entry.fg_completed_qty = 1
		transfer_entry.get_items()
		transfer_entry.insert()
		transfer_entry.submit()

		job_card.reload()
		self.assertEqual(job_card.transferred_qty, 1)
		self.assertEqual(transfer_entry.items[0].qty, 5)
		self.assertEqual(transfer_entry.items[1].qty, 3)

		# transfer remaining
		transfer_entry_2 = make_stock_entry_from_jc(job_card_name)

		self.assertEqual(transfer_entry_2.fg_completed_qty, 1)
		self.assertEqual(transfer_entry_2.items[0].qty, 5)
		self.assertEqual(transfer_entry_2.items[1].qty, 3)

		transfer_entry_2.insert()
		transfer_entry_2.submit()

		job_card.reload()
		self.assertEqual(job_card.transferred_qty, 2)

	def test_job_card_material_transfer_correctness(self):
		"""
			1. Test if only current Job Card Items are pulled in a Stock Entry against a Job Card
			2. Test impact of changing 'For Qty' in such a Stock Entry
		"""
		bom = create_bom_with_multiple_operations()
		work_order = make_wo_with_transfer_against_jc()

		job_card_name = frappe.db.get_value(
			"Job Card",
			{"work_order": work_order.name,"operation": "Test Operation A"}
		)
		job_card = frappe.get_doc("Job Card", job_card_name)

		self.assertEqual(len(job_card.items), 1)
		self.assertEqual(job_card.items[0].item_code, "_Test Item")

		# check if right items are mapped in transfer entry
		transfer_entry = make_stock_entry_from_jc(job_card_name)
		transfer_entry.insert()

		self.assertEqual(len(transfer_entry.items), 1)
		self.assertEqual(transfer_entry.items[0].item_code, "_Test Item")
		self.assertEqual(transfer_entry.items[0].qty, 4)

		# change 'For Qty' and check impact on items table
		# no.of items should be the same with qty change
		transfer_entry.fg_completed_qty = 2
		transfer_entry.get_items()

		self.assertEqual(len(transfer_entry.items), 1)
		self.assertEqual(transfer_entry.items[0].item_code, "_Test Item")
		self.assertEqual(transfer_entry.items[0].qty, 2)

		# rollback via tearDown method

def create_bom_with_multiple_operations():
	"Create a BOM with multiple operations and Material Transfer against Job Card"
	from erpnext.manufacturing.doctype.operation.test_operation import make_operation

	test_record = frappe.get_test_records("BOM")[2]
	bom_doc = frappe.get_doc(test_record)

	row = {
		"operation": "Test Operation A",
		"workstation": "_Test Workstation A",
		"hour_rate_rent": 300,
		"time_in_mins": 60
	}
	make_workstation(row)
	make_operation(row)

	bom_doc.append("operations", {
		"operation": "Test Operation A",
		"description": "Test Operation A",
		"workstation": "_Test Workstation A",
		"hour_rate": 300,
		"time_in_mins": 60,
		"operating_cost": 100
	})

	bom_doc.transfer_material_against = "Job Card"
	bom_doc.save()
	bom_doc.submit()

	return bom_doc

def make_wo_with_transfer_against_jc():
	"Create a WO with multiple operations and Material Transfer against Job Card"

	work_order = make_wo_order_test_record(
		item="_Test FG Item 2",
		qty=4,
		transfer_material_against="Job Card",
		source_warehouse="Stores - _TC",
		do_not_submit=True
	)
	work_order.required_items[0].operation = "Test Operation A"
	work_order.required_items[1].operation = "_Test Operation 1"
	work_order.submit()

	return work_order