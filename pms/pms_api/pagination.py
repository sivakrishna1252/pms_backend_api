from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data, *, message="Data fetched successfully."):
        return Response(
            {
                "success": True,
                "message": message,
                "code": status.HTTP_200_OK,
                "data": {
                    "results": data,
                },
                "meta": self._meta_payload(),
            }
        )

    def get_paginated_data_response(self, data, *, message="Data fetched successfully.", **extra):
        return Response(
            {
                "success": True,
                "message": message,
                "code": status.HTTP_200_OK,
                "data": {
                    "results": data,
                    **extra,
                },
                "meta": self._meta_payload(),
            }
        )

    def _meta_payload(self):
        return {
            "page": self.page.number,
            "page_size": self.get_page_size(self.request),
            "total": self.page.paginator.count,
            "total_pages": self.page.paginator.num_pages,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
        }


def paginate_request(request, queryset_or_list):
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset_or_list, request)
    return page, paginator


def unpaginated_list_response(data, *, message="Data fetched successfully.", **extra):
    payload = {"results": data, **extra}
    return Response(
        {
            "success": True,
            "message": message,
            "code": status.HTTP_200_OK,
            "data": payload,
        }
    )
