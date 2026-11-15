"""Initial schema: customers, drivers, orders, order_events

Revision ID: 357e85ccdb45
Revises: 
Create Date: 2026-11-15 17:36:45.496631
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '357e85ccdb45'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('customers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone')
    )
    op.create_table('drivers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('status', sa.Enum('offline', 'available', 'busy', name='driverstatus', native_enum=False, length=20), nullable=False),
    sa.Column('last_lat', sa.Float(), nullable=True),
    sa.Column('last_lng', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone')
    )
    op.create_index('ix_drivers_status', 'drivers', ['status'])

    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('customer_name', sa.String(length=100), nullable=False),
    sa.Column('pickup_lat', sa.Float(), nullable=False),
    sa.Column('pickup_lng', sa.Float(), nullable=False),
    sa.Column('dropoff_lat', sa.Float(), nullable=False),
    sa.Column('dropoff_lng', sa.Float(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'assigned', 'picked_up', 'delivered', 'cancelled', name='orderstatus', native_enum=False, length=20), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('dispatch_attempts', sa.Integer(), nullable=False),
    sa.Column('assignment_distance_km', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('picked_up_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['driver_id'], ['drivers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_orders_created_at', 'orders', ['created_at'])
    op.create_index('ix_orders_customer_id', 'orders', ['customer_id'])
    op.create_index('ix_orders_driver_id', 'orders', ['driver_id'])
    op.create_index('ix_orders_status', 'orders', ['status'])

    op.create_table('order_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'assigned', 'picked_up', 'delivered', 'cancelled', name='orderstatus', native_enum=False, length=20), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_order_events_order_id', 'order_events', ['order_id'])



def downgrade() -> None:
    op.drop_index('ix_order_events_order_id', table_name='order_events')

    op.drop_table('order_events')
    op.drop_index('ix_orders_status', table_name='orders')
    op.drop_index('ix_orders_driver_id', table_name='orders')
    op.drop_index('ix_orders_customer_id', table_name='orders')
    op.drop_index('ix_orders_created_at', table_name='orders')

    op.drop_table('orders')
    op.drop_index('ix_drivers_status', table_name='drivers')

    op.drop_table('drivers')
    op.drop_table('customers')
