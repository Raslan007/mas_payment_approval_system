# RBAC Review

نظرة على كل Route مفعّل حالياً، مع توضيح من يملك حق الوصول وكيف يتم التحقق فعلياً، وأي ثغرات أو ملاحظات مع اقتراح Fix بسيط عند الحاجة.

## الملاحظات العامة
- كل المسارات المحمية تستخدم `@role_required` الذي يضيف `login_required` تلقائياً ويمنع الأدوار غير المصرح بها، مع صلاحيات خاصة لـ `admin` (وصول كامل) و`chairman` (قراءة فقط).
- مسارات الإشعارات تستخدم `@login_required` فقط لكنها تتحقق من هوية المستخدم في الاستعلامات (`user_id == current_user.id`) مما يمنع الوصول للغير.
- لا توجد مسارات عامة مفتوحة سوى صفحة تسجيل الدخول `/auth/login` المتوقع أن تكون عامة.
- لا توجد مسارات غير مسجلة: الـ blueprint الخاصة بالإشعارات غير مضافة في `app.py` وبالتالي لا تتعرض لفتح غير مقصود (لكن هذا يعني أن الإشعارات غير متاحة حالياً).

## التغطية التفصيلية حسب الـ Blueprint

### main
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix | 
| --- | --- | --- | --- |
| `/` | أي مستخدم مسجل دخول | `@login_required` ثم توجيه حسب الدور | لا توجد مشكلات. |
| `/dashboard` | admin, engineering_manager, chairman, finance | `@role_required(...)` | لا توجد مشكلات. |
| `/eng-dashboard` | admin, engineering_manager, chairman | `@role_required(...)` | لا توجد مشكلات. |

### auth
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix |
| --- | --- | --- | --- |
| `/auth/login` (GET/POST) | عام | التحقق من البريد/كلمة المرور فقط | متوقع أن تكون عامة. |
| `/auth/logout` | أي مستخدم مسجل | `@login_required` | لا توجد مشكلات. |

### users
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix |
| --- | --- | --- | --- |
| `/users/`, `/users/list` | admin, dc | `@role_required("admin","dc")` | لا توجد مشكلات. |
| `/users/create` | admin, dc | `@role_required(...)` مع تحقق بيانات | لا توجد مشكلات. |
| `/users/<id>/edit` | admin, dc | `@role_required(...)` مع تحقق إضافي | لا توجد مشكلات. |
| `/users/<id>/delete` | admin, dc | `@role_required(...)` ويمنع حذف النفس | لا توجد مشكلات. |

### projects
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix |
| --- | --- | --- | --- |
| `/projects/`, `/projects/list` | admin, engineering_manager, dc | `@role_required(...)` | لا توجد مشكلات. |
| `/projects/create` | admin, engineering_manager, dc | `@role_required(...)` | لا توجد مشكلات. |
| `/projects/<id>/edit` | admin, engineering_manager, dc | `@role_required(...)` | لا توجد مشكلات. |

### suppliers
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix |
| --- | --- | --- | --- |
| `/suppliers/`, `/suppliers/list` | admin, engineering_manager, dc | `@role_required(...)` | لا توجد مشكلات. |
| `/suppliers/create` | admin, engineering_manager, dc | `@role_required(...)` | لا توجد مشكلات. |
| `/suppliers/<id>/edit` | admin, engineering_manager, dc | `@role_required(...)` | لا توجد مشكلات. |
| `/suppliers/<id>/delete` | admin, engineering_manager | `@role_required(...)` | لا توجد مشكلات. |

### payments
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix |
| --- | --- | --- | --- |
| `/payments/`, `/payments/my` | admin, engineering_manager, project_manager, engineer, finance, chairman | `@role_required(...)` + تصفية حسب الدور في الكويري | لا توجد مشكلات. |
| `/payments/all` | admin, engineering_manager, chairman | `@role_required(...)` | لا توجد مشكلات. |
| `/payments/pm_review` | admin, engineering_manager, project_manager, chairman | `@role_required(...)` | لا توجد مشكلات. |
| `/payments/eng_review` | admin, engineering_manager, chairman | `@role_required(...)` | لا توجد مشكلات. |
| `/payments/finance_review` | admin, engineering_manager, finance, chairman | `@role_required(...)` | لا توجد مشكلات. |
| `/payments/finance_eng_approved` | admin, engineering_manager, finance, chairman | `@role_required(...)` | لا توجد مشكلات. |
| `/payments/create` | admin, engineering_manager, project_manager, engineer | `@role_required(...)` | لا توجد مشكلات. |
| `/payments/<id>` | admin, engineering_manager, project_manager, engineer, finance, chairman | `@role_required(...)` + `_require_can_view` للتقييد حسب الحالة/المالك | لا توجد مشكلات. |
| `/payments/<id>/edit` | admin, engineering_manager, project_manager, engineer | `@role_required(...)` + `_require_can_edit` | لا توجد مشكلات. |
| `/payments/<id>/delete` | admin, engineering_manager | `@role_required(...)` + `_require_can_delete` | لا توجد مشكلات. |
| Workflow POST routes (`submit_to_pm`, `pm_approve/reject`, `eng_approve/reject`, `finance_approve/reject`, `mark_paid`) | حسب كل Route المذكور في الكود | `@role_required(...)` + `_require_can_view` لتأكيد الصلاحية والسياق | لا توجد مشكلات. |

### notifications
| Route | الأدوار المسموح بها | التحقق الفعلي | ملاحظات / Fix |
| --- | --- | --- | --- |
| `/notifications/` | أي مستخدم مسجل | `@login_required` + تصفية `user_id` | لا توجد مشكلات. |
| `/notifications/<id>/read` | أي مستخدم مسجل | `@login_required` + تطابق `user_id` أو 403 | لا توجد مشكلات. |
| `/notifications/mark-all-read` | أي مستخدم مسجل | `@login_required` + تصفية `user_id` | لا توجد مشكلات. |

## الخلاصة
- لم يتم العثور على مسارات يمكن فتحها بدون صلاحية مناسبة بعد مراجعة كل الـ routes.
- لا توجد إصلاحات مطلوبة حاليًا؛ الاستمرار في الالتزام باستخدام `role_required` لجميع المسارات الحساسة هو الحد الأدنى المتبع بالفعل.
- توصية صغيرة: إضافة الـ blueprint الخاص بالإشعارات في `app.py` إذا كانت ميزة الإشعارات مطلوبة فعليًا، مع الحفاظ على نفس حراسة الدخول الحالية.
