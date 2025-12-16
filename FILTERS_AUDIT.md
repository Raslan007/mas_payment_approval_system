# Filters Audit

ملخص لأماكن الفلاتر في النظام وما تم إصلاحه.

## /payments/ و /payments/my
- **Parameters**: `project_id`, `request_type`, `status`, `week_number`, `date_from`, `date_to`, `page`, `per_page` (كلها من `request.args`).
- **Usage**: تُستخدم لبناء استعلام `PaymentRequest` مع تحميل مسبق للمشروع/المورد/المنشئ وترتيب ثابت `created_at DESC, id DESC`.
- **Risk**: عدم ضبط الحدود لأنواع البيانات، احتمالية تمرير project_id خارج صلاحيات مدير المشروع، قيم status/request_type غير متوقعة، page/per_page سالبة أو كبيرة، واحتمال الاعتماد على جدول user_projects غير موجود.
- **Fix**: تنظيف القيم (تحويل رقمي آمن، حدود week_number 1–53، تواريخ مصفّاة)، تقليم per_page إلى 1–100، قائمة بيضاء للحالات وأنواع الدفعات، منع رؤية مدير المشروع لمشاريع غير مسموح بها حتى مع تمرير project_id، عدّ إجمالي مستقل عن التحميل المسبق، التعامل الآمن مع غياب جدول `user_projects`.

## /payments/finance_eng_approved
- **Parameters**: `project_id`, `supplier_id`, `request_type`, `date_from`, `date_to` من `request.args`.
- **Usage**: تصفية `PaymentRequest` بحالة `ready_for_payment` مع تحميل العلاقات وترتيب ثابت.
- **Risk**: قيم غير رقمية للمشاريع/الموردين أو تواريخ غير صالحة تؤدي إلى استعلامات خاطئة أو أخطاء.
- **Fix**: تنظيف الأرقام والتواريخ مع fallback، قائمة بيضاء لأنواع الدفعات، وترتيب حتمي.

## /dashboard
- **Parameters**: `page`, `per_page` من `request.args`.
- **Usage**: صفحات لوحة التحكم العامة للدفعات مع إحصاءات مجمعة وترتيب ثابت.
- **Risk**: قيم غير رقمية قد تُسقط الاستعلام؛ التصفح غير مقيد الحجم.
- **Fix**: تحويل آمن مع حدود `page>=1` و`per_page<=100` موجودة مسبقًا.

## /eng-dashboard
- **Parameters**: `project_id`, `status`, `date_from`, `date_to` من `request.args`.
- **Usage**: فلاتر إحصائية للإدارة الهندسية على استعلام `PaymentRequest`.
- **Risk**: قيم غير صالحة للمشروع أو التواريخ أو الحالة تتسبب بتجاهل الفلاتر أو أخطاء صامتة.
- **Fix**: تنظيف الأرقام والتواريخ، قائمة بيضاء للحالات، وإبقاء القيم المنظّفة معروضة في النموذج.

## Pagination links
- **Parameters preserved**: الروابط (Prev/Next/Per-page) تحفظ الفلاتر النشطة عبر `query_params` المعقّمة لضمان اتساق التنقل دون توسيع الصلاحيات.
